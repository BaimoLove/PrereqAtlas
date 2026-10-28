"""M06 图谱管理的持久化访问：版本、关系、生效指针与审计。

只做查询与写入，不校验草稿状态、不决定事务边界；状态与 revision 语义在 service。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.graph_admin.models import ActiveGraph, GraphAudit, GraphRelation, GraphVersion


@dataclass(frozen=True, slots=True)
class NewRelation:
    """待插入的一条关系；两端是已解析的概念稳定身份。"""

    source_concept_id: int
    target_concept_id: int
    necessity: str = "REQUIRED"
    structural_type: str | None = None
    source_strength: Decimal | None = None
    review_status: str = "PENDING"
    rationale: str | None = None
    source_batch_id: int | None = None
    source_edge_key: str | None = None


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


def get_relation(
    session: Session, version_id: int, relation_id: int
) -> GraphRelation | None:
    """按版本与主键读取关系；他版本的关系等价于不存在。"""
    statement = select(GraphRelation).where(
        GraphRelation.version_id == version_id, GraphRelation.id == relation_id
    )
    return session.scalars(statement).first()


def find_relation(
    session: Session,
    version_id: int,
    *,
    source_concept_id: int,
    target_concept_id: int,
    necessity: str,
) -> GraphRelation | None:
    """同版本内同向同必要性的关系；用于重复边预检。"""
    statement = select(GraphRelation).where(
        GraphRelation.version_id == version_id,
        GraphRelation.source_concept_id == source_concept_id,
        GraphRelation.target_concept_id == target_concept_id,
        GraphRelation.necessity == necessity,
    )
    return session.scalars(statement).first()


def list_relations_by_ids(
    session: Session, version_id: int, relation_ids: Sequence[int]
) -> list[GraphRelation]:
    """只返回属于该版本的关系；调用方据缺失集合判定整批失败。"""
    if not relation_ids:
        return []
    statement = (
        select(GraphRelation)
        .where(
            GraphRelation.version_id == version_id,
            GraphRelation.id.in_(relation_ids),
        )
        .order_by(GraphRelation.id)
    )
    return list(session.scalars(statement))


def delete_relation(session: Session, relation: GraphRelation) -> None:
    session.delete(relation)
    session.flush()


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


def add_relations(
    session: Session, *, version_id: int, relations: Sequence[NewRelation]
) -> list[GraphRelation]:
    """插入一批关系并返回持久化后的行；唯一键冲突由调用方转成业务错误。"""
    rows = [
        GraphRelation(
            version_id=version_id,
            source_concept_id=relation.source_concept_id,
            target_concept_id=relation.target_concept_id,
            necessity=relation.necessity,
            structural_type=relation.structural_type,
            source_strength=relation.source_strength,
            review_status=relation.review_status,
            rationale=relation.rationale,
            source_batch_id=relation.source_batch_id,
            source_edge_key=relation.source_edge_key,
        )
        for relation in relations
    ]
    session.add_all(rows)
    session.flush()
    return rows


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
