"""M06 图谱管理的持久化访问：版本、关系、生效指针与审计。

只做查询与写入，不校验草稿状态、不决定事务边界；状态与 revision 语义在 service。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.graph_admin.models import ActiveGraph, GraphAudit, GraphRelation, GraphVersion


def get_version(session: Session, version_id: int) -> GraphVersion | None:
    return session.get(GraphVersion, version_id)


def lock_version(session: Session, version_id: int) -> GraphVersion | None:
    """按行加锁读取版本，供草稿写入口与发布入口使用。"""
    statement = select(GraphVersion).where(GraphVersion.id == version_id).with_for_update()
    return session.scalars(statement).first()


def find_version_by_label(session: Session, version_label: str) -> GraphVersion | None:
    statement = select(GraphVersion).where(GraphVersion.version_label == version_label)
    return session.scalars(statement).first()


def create_version(
    session: Session,
    *,
    version_label: str,
    base_version_id: int | None,
    created_by: int,
    draft_revision: int,
) -> GraphVersion:
    version = GraphVersion(
        version_label=version_label,
        state="DRAFT",
        base_version_id=base_version_id,
        draft_revision=draft_revision,
        created_by=created_by,
    )
    session.add(version)
    session.flush()
    return version


def count_versions(session: Session, *, state: str | None) -> int:
    statement = select(func.count()).select_from(GraphVersion)
    if state is not None:
        statement = statement.where(GraphVersion.state == state)
    return int(session.scalar(statement) or 0)


def list_versions(
    session: Session, *, state: str | None, offset: int, limit: int
) -> list[GraphVersion]:
    """按创建时间、主键倒序稳定分页。"""
    statement = select(GraphVersion)
    if state is not None:
        statement = statement.where(GraphVersion.state == state)
    statement = (
        statement.order_by(GraphVersion.created_at.desc(), GraphVersion.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(session.scalars(statement))


def get_active_graph(session: Session) -> ActiveGraph | None:
    return session.get(ActiveGraph, 1)


def list_relations(session: Session, version_id: int) -> list[GraphRelation]:
    statement = (
        select(GraphRelation)
        .where(GraphRelation.version_id == version_id)
        .order_by(GraphRelation.id)
    )
    return list(session.scalars(statement))


def review_counts(session: Session, version_id: int) -> dict[str, int]:
    counts = {"APPROVED": 0, "REJECTED": 0, "PENDING": 0}
    statement = (
        select(GraphRelation.review_status, func.count())
        .where(GraphRelation.version_id == version_id)
        .group_by(GraphRelation.review_status)
    )
    for status, count in session.execute(statement).all():
        counts[str(status)] = int(count)
    return counts


def copy_relations(
    session: Session, *, source_version_id: int, target_version_id: int
) -> int:
    """复制来源版本的全部关系记录，保留审核人、时间、理由与来源追溯字段。"""
    relations = list_relations(session, source_version_id)
    session.add_all(
        [
            GraphRelation(
                version_id=target_version_id,
                source_concept_id=relation.source_concept_id,
                target_concept_id=relation.target_concept_id,
                necessity=relation.necessity,
                structural_type=relation.structural_type,
                source_strength=relation.source_strength,
                review_status=relation.review_status,
                rationale=relation.rationale,
                source_batch_id=relation.source_batch_id,
                source_edge_key=relation.source_edge_key,
                reviewed_by=relation.reviewed_by,
                reviewed_at=relation.reviewed_at,
            )
            for relation in relations
        ]
    )
    session.flush()
    return len(relations)


def add_audit(
    session: Session,
    *,
    version_id: int,
    operator_id: int,
    action: str,
    revision_before: int | None,
    revision_after: int | None,
    summary: str,
) -> GraphAudit:
    audit = GraphAudit(
        version_id=version_id,
        operator_id=operator_id,
        action=action,
        revision_before=revision_before,
        revision_after=revision_after,
        summary=summary,
    )
    session.add(audit)
    session.flush()
    return audit
