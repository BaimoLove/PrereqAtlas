"""请求事务依赖。"""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.common.contracts import Actor, ErrorCode, IdStr, Role, parse_id
from app.common.http_contracts import ConceptDetailQuery, ConceptSearchQuery
from app.infra.db import session_scope
from app.infra.errors import AppError
from app.modules.graph_admin.public import (
    PublishedGraphContext,
    pin_active_version,
    read_published_version,
)
from app.modules.graph_runtime.public import SnapshotCache
from app.modules.identity.public import resolve_actor


def get_session() -> Iterator[Session]:
    with session_scope() as session:
        yield session


_bearer = HTTPBearer(auto_error=False)


def get_actor(
    token: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[Session, Depends(get_session, scope="function")],
) -> Actor:
    return resolve_actor(session, token.credentials if token else "")


def require_admin(actor: Annotated[Actor, Depends(get_actor)]) -> Actor:
    if actor.role != Role.ADMIN:
        raise AppError(ErrorCode.FORBIDDEN)
    return actor


def require_student(actor: Annotated[Actor, Depends(get_actor)]) -> Actor:
    if actor.role != Role.STUDENT:
        raise AppError(ErrorCode.FORBIDDEN)
    return actor


def get_snapshot_cache(request: Request) -> SnapshotCache:
    """缓存属于应用进程；重启后从数据库的固定发布版重建。"""
    cache: SnapshotCache = request.app.state.snapshot_cache
    return cache


def get_active_context(
    actor: Annotated[Actor, Depends(get_actor)],
    session: Annotated[Session, Depends(get_session, scope="function")],
    cache: Annotated[SnapshotCache, Depends(get_snapshot_cache)],
) -> PublishedGraphContext:
    return pin_active_version(session, cache=cache)


def get_version_context(
    version_id: IdStr,
    actor: Annotated[Actor, Depends(get_actor)],
    session: Annotated[Session, Depends(get_session, scope="function")],
    cache: Annotated[SnapshotCache, Depends(get_snapshot_cache)],
) -> PublishedGraphContext:
    return read_published_version(session, parse_id(version_id), cache=cache)


def _published_context(
    session: Session, cache: SnapshotCache, version_id: str | None
) -> PublishedGraphContext:
    """省略版本时固定当前生效发布版；否则读取指定发布版。"""
    if version_id is None:
        return pin_active_version(session, cache=cache)
    return read_published_version(session, parse_id(version_id), cache=cache)


def search_query(
    query: Annotated[ConceptSearchQuery, Query()],
) -> ConceptSearchQuery:
    """解析一次列表查询参数，使路由与上下文依赖共用同一实例。"""
    return query


def detail_query(
    query: Annotated[ConceptDetailQuery, Query()],
) -> ConceptDetailQuery:
    """解析一次知识卡查询参数。"""
    return query


def get_query_context(
    query: Annotated[ConceptSearchQuery, Depends(search_query)],
    actor: Annotated[Actor, Depends(get_actor)],
    session: Annotated[Session, Depends(get_session, scope="function")],
    cache: Annotated[SnapshotCache, Depends(get_snapshot_cache)],
) -> PublishedGraphContext:
    return _published_context(session, cache, query.version_id)


def get_detail_context(
    query: Annotated[ConceptDetailQuery, Depends(detail_query)],
    actor: Annotated[Actor, Depends(get_actor)],
    session: Annotated[Session, Depends(get_session, scope="function")],
    cache: Annotated[SnapshotCache, Depends(get_snapshot_cache)],
) -> PublishedGraphContext:
    return _published_context(session, cache, query.version_id)
