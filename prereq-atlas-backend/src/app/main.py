"""应用工厂、生命周期与总路由装配。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.dependencies import get_snapshot_cache
from app.api.router import api_router
from app.common.contracts import ErrorCode
from app.common.http_contracts import HealthLiveResponse, HealthReadyResponse
from app.infra.config import get_settings
from app.infra.db import get_engine
from app.infra.errors import AppError, install_error_handlers
from app.infra.logging import configure_logging
from app.infra.middleware import HeadMethodMiddleware, RequestContextMiddleware
from app.modules.graph_admin.public import pin_active_version
from app.modules.graph_runtime.public import SnapshotCache


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """不在启动时要求生效图谱；应用工厂创建空缓存，读取时按固定版本重建。"""
    yield


def create_app() -> FastAPI:
    """构建应用实例；业务模块路由在各自的 router 中就绪后由此聚合。"""
    settings = get_settings()
    configure_logging(settings)
    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.state.snapshot_cache = SnapshotCache()
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(HeadMethodMiddleware)
    install_error_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/health/live", tags=["health"], summary="存活检查")
    def health_live() -> HealthLiveResponse:
        """进程存活即返回 200；不检查数据库、生效版本或图模型。"""
        return HealthLiveResponse()

    @app.get("/health/ready", tags=["health"], summary="就绪检查")
    def health_ready(
        engine: Annotated[Engine, Depends(get_engine)],
        cache: Annotated[SnapshotCache, Depends(get_snapshot_cache)],
    ) -> HealthReadyResponse:
        """数据库、生效发布版与同版运行图均可用时才就绪。"""
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            with Session(engine) as session:
                pin_active_version(session, cache=cache)
        except SQLAlchemyError as exc:
            raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "数据库不可用") from exc
        return HealthReadyResponse()

    return app


app = create_app()
