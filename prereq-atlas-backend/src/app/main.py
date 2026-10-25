"""应用工厂、生命周期与总路由装配。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.api.router import api_router
from app.infra.config import get_settings
from app.infra.db import get_engine


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """首期无外部资源需初始化和释放；数据库引擎与图缓存以后在此挂载。"""
    yield


def create_app() -> FastAPI:
    """构建应用实例；业务模块路由在各自的 router 中就绪后由此聚合。"""
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/health", tags=["health"], summary="存活检查")
    def health() -> dict[str, str]:
        """进程存活即返回 200；不检查数据库、生效版本或图模型。"""
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"], summary="就绪检查")
    def readiness(engine: Annotated[Engine, Depends(get_engine)]) -> dict[str, str]:
        """数据库连通性；生效图谱版本与图模型可用性待后续实现。"""
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            raise HTTPException(status_code=503, detail="数据库不可用") from exc
        return {"status": "ready", "database": "ok"}

    return app


app = create_app()
