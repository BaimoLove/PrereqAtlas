"""M06 发布事务：内容与生效指针同事务，缓存仅在提交后预热。"""

from __future__ import annotations

import hashlib
import json
import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.common.contracts import Actor, ErrorCode, GraphState, ReviewStatus, parse_id
from app.common.http_contracts import PublishDraftRequest, PublishResponse
from app.common.time import now_beijing
from app.infra.errors import AppError
from app.modules.graph_admin import repository, service
from app.modules.graph_admin.models import ActiveGraph
from app.modules.graph_runtime import public as runtime

logger = logging.getLogger("app.publication")


def _publish_draft(
    session: Session,
    draft_id: str,
    *,
    actor: Actor,
    request: PublishDraftRequest,
    cache: runtime.SnapshotCache,
) -> PublishResponse:
    service._require_admin(actor)
    # 身份认证查询可能已建立 REPEATABLE READ 读视图。发布独占事务，先结束该只读事务，
    # 再按 active → draft 加锁，使后续内容读取来自锁定 revision 而非认证时刻。
    session.rollback()
    active = session.scalar(
        select(ActiveGraph).where(ActiveGraph.singleton_id == 1).with_for_update()
    )
    actual = active.revision if active is not None else 0
    if actual != request.expected_active_revision:
        raise AppError(
            ErrorCode.CONFLICT,
            "生效图谱版本号已变化",
            {
                "expected_revision": request.expected_active_revision,
                "actual_revision": actual,
            },
        )
    version = service._lock_writable_draft(session, parse_id(draft_id), request.expected_revision)
    relations = repository.list_relations(session, version.id)
    data = service._version_data(session, version, relations)
    if version.validated_revision != version.draft_revision:
        raise AppError(ErrorCode.CONFLICT, "请先校验当前草稿版本")
    if service._chapter_issues(data) or any(
        r.review_status != ReviewStatus.APPROVED.value for r in relations
    ):
        raise AppError(ErrorCode.VALIDATION_ERROR, "发布复验未通过")
    try:
        runtime.build_snapshot(data)
    except runtime.GraphDataError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, "发布复验未通过") from exc
    payload = data.model_dump(mode="json", exclude={"version_id", "content_hash"})
    payload["review_records"] = [
        {
            "relation_id": r.id,
            "reviewed_by": r.reviewed_by,
            "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
            "source_batch_id": r.source_batch_id,
            "source_edge_key": r.source_edge_key,
            "source_strength": str(r.source_strength) if r.source_strength is not None else None,
        }
        for r in relations
    ]
    content_hash = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    data = data.model_copy(update={"content_hash": content_hash})
    version.state = GraphState.PUBLISHED.value
    version.content_hash = content_hash
    version.published_by = actor.user_id
    version.published_at = now_beijing()
    if active is None:
        active = ActiveGraph(
            singleton_id=1, version_id=version.id, revision=1, updated_by=actor.user_id
        )
        session.add(active)
    else:
        if not repository.advance_active_graph(
            session,
            expected_revision=actual,
            version_id=version.id,
            operator_id=actor.user_id,
        ):
            raise AppError(ErrorCode.CONFLICT, "生效指针已变化")
    repository.add_audit(
        session,
        version_id=version.id,
        operator_id=actor.user_id,
        action="PUBLISH",
        revision_before=version.draft_revision,
        revision_after=version.draft_revision,
        summary=f"发布 {len(data.nodes)} 概念/{len(data.edges)} 关系；SHA-256 {content_hash}",
    )
    response = PublishResponse(
        version_id=str(version.id),
        published=True,
        runtime_ready=True,
        active_revision=actual + 1,
    )
    session.commit()
    try:
        cache.get_or_build(data)
    except Exception:
        logger.error("version_id=%s published graph cache warmup failed", data.version_id)
        response.runtime_ready = False
    return response


def publish_draft(
    session: Session,
    draft_id: str,
    *,
    actor: Actor,
    request: PublishDraftRequest,
    cache: runtime.SnapshotCache,
) -> PublishResponse:
    """单例首插死锁/唯一键竞争属于并发冲突；事务失败必须先完整回滚。"""
    try:
        return _publish_draft(session, draft_id, actor=actor, request=request, cache=cache)
    except (IntegrityError, OperationalError) as exc:
        session.rollback()
        number = exc.orig.args[0] if exc.orig is not None and exc.orig.args else None
        if number in {1062, 1205, 1213}:
            raise AppError(ErrorCode.CONFLICT, "发布竞争冲突，请刷新生效版本后重试") from exc
        raise
    except BaseException:
        session.rollback()
        raise
