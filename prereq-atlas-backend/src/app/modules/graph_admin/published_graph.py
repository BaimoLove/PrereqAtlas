"""发布内容只读面：一次固定版本，缓存失败不回退旧版本。"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.common.contracts import ErrorCode, GraphState, VersionData
from app.infra.errors import AppError
from app.modules.graph_admin import repository, service
from app.modules.graph_runtime.public import GraphSnapshot, SnapshotCache


@dataclass(frozen=True, slots=True)
class PublishedGraphContext:
    version_id: int
    label: str
    content_hash: str
    active_revision: int | None
    data: VersionData
    snapshot: GraphSnapshot


def read_published_version(
    session: Session,
    version_id: int,
    *,
    cache: SnapshotCache,
    active_revision: int | None = None,
) -> PublishedGraphContext:
    try:
        version = repository.get_version(session, version_id)
    except SQLAlchemyError as exc:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "发布图谱暂不可用") from exc
    if version is None or version.state != GraphState.PUBLISHED.value:
        raise AppError(ErrorCode.NOT_FOUND, "发布版本不存在")
    if version.content_hash is None:
        # DDL 的 CHECK 约束已保证 PUBLISHED 必有哈希；此处防的是约束之外的历史数据，
        # 且必须在构图之前拦截，否则会以 (version_id, None) 写入一份脏缓存键。
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "发布图谱暂不可用")
    try:
        data = service._version_data(
            session, version, repository.list_relations(session, version_id)
        )
        snapshot = cache.get_or_build(data)
    except Exception as exc:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "发布图谱暂不可用") from exc
    return PublishedGraphContext(
        version.id, version.version_label, version.content_hash, active_revision, data, snapshot
    )


def pin_active_version(session: Session, *, cache: SnapshotCache) -> PublishedGraphContext:
    try:
        active = repository.get_active_graph(session)
    except SQLAlchemyError as exc:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "生效图谱暂不可用") from exc
    if active is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "尚无生效图谱")
    version_id, revision = active.version_id, active.revision
    try:
        return read_published_version(session, version_id, cache=cache, active_revision=revision)
    except AppError as exc:
        if exc.code is ErrorCode.NOT_FOUND:
            raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "生效图谱不可用") from exc
        raise
