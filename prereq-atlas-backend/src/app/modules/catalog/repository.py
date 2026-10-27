"""M02 知识目录的持久化访问。

只负责 SQLAlchemy 查询与写入：概念稳定身份、章节版本、概念版本与资料版本。
不校验业务规则（同版引用、身份冲突由 service 负责），也不提交事务。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.contracts import VersionNode, VersionResource
from app.modules.catalog.models import (
    ChapterRevision,
    Concept,
    ConceptRevision,
    ResourceRevision,
)


def get_chapter(session: Session, version_id: int, chapter_code: str) -> ChapterRevision | None:
    return session.get(ChapterRevision, (version_id, chapter_code))


def list_chapters(session: Session, version_id: int) -> list[ChapterRevision]:
    statement = (
        select(ChapterRevision)
        .where(ChapterRevision.version_id == version_id)
        .order_by(ChapterRevision.display_order, ChapterRevision.chapter_code)
    )
    return list(session.scalars(statement))


def upsert_chapter(
    session: Session,
    *,
    version_id: int,
    chapter_code: str,
    name: str,
    display_order: int,
) -> ChapterRevision:
    row = session.get(ChapterRevision, (version_id, chapter_code))
    if row is None:
        row = ChapterRevision(
            version_id=version_id,
            chapter_code=chapter_code,
            name=name,
            display_order=display_order,
        )
        session.add(row)
    else:
        row.name = name
        row.display_order = display_order
    session.flush()
    return row


def get_concept(session: Session, concept_id: int) -> Concept | None:
    return session.get(Concept, concept_id)


def find_concept_by_source(
    session: Session, source_namespace: str, source_key: str
) -> Concept | None:
    statement = select(Concept).where(
        Concept.source_namespace == source_namespace,
        Concept.source_key == source_key,
    )
    return session.scalars(statement).first()


def find_concept_by_code(session: Session, code: str) -> Concept | None:
    statement = select(Concept).where(Concept.code == code)
    return session.scalars(statement).first()


def create_concept(
    session: Session,
    *,
    code: str,
    source_namespace: str | None,
    source_key: str | None,
) -> Concept:
    concept = Concept(code=code, source_namespace=source_namespace, source_key=source_key)
    session.add(concept)
    session.flush()
    return concept


def get_concept_revision(
    session: Session, version_id: int, concept_id: int
) -> ConceptRevision | None:
    return session.get(ConceptRevision, (version_id, concept_id))


def get_concept_code(session: Session, concept_id: int) -> str | None:
    concept = session.get(Concept, concept_id)
    return None if concept is None else concept.code


def upsert_concept_revision(
    session: Session,
    *,
    version_id: int,
    concept_id: int,
    label_zh: str,
    label_en: str | None,
    chapter_code: str | None,
    node_type: str | None,
    introduction: str | None,
    learning_focus: str | None,
) -> ConceptRevision:
    row = session.get(ConceptRevision, (version_id, concept_id))
    if row is None:
        row = ConceptRevision(version_id=version_id, concept_id=concept_id)
        session.add(row)
    row.label_zh = label_zh
    row.label_en = label_en
    row.chapter_code = chapter_code
    row.node_type = node_type
    row.introduction = introduction
    row.learning_focus = learning_focus
    session.flush()
    return row


def list_resources(
    session: Session, version_id: int, *, concept_id: int | None = None
) -> list[ResourceRevision]:
    statement = select(ResourceRevision).where(ResourceRevision.version_id == version_id)
    if concept_id is not None:
        statement = statement.where(ResourceRevision.concept_id == concept_id)
    return list(session.scalars(statement.order_by(ResourceRevision.resource_id)))


def add_resource(
    session: Session,
    *,
    version_id: int,
    concept_id: int,
    title: str,
    url: str,
    source: str | None,
) -> ResourceRevision:
    resource = ResourceRevision(
        version_id=version_id,
        concept_id=concept_id,
        title=title,
        url=url,
        source=source,
    )
    session.add(resource)
    session.flush()
    return resource


def list_nodes(session: Session, version_id: int) -> list[VersionNode]:
    """按概念编码稳定排序返回整版概念内容，资料随概念返回。"""
    statement = (
        select(ConceptRevision, Concept.code)
        .join(Concept, Concept.id == ConceptRevision.concept_id)
        .where(ConceptRevision.version_id == version_id)
        .order_by(Concept.code, ConceptRevision.concept_id)
    )
    rows = session.execute(statement).all()
    grouped: dict[int, list[VersionResource]] = {}
    for resource in list_resources(session, version_id):
        grouped.setdefault(resource.concept_id, []).append(
            VersionResource(
                resource_id=resource.resource_id,
                title=resource.title,
                url=resource.url,
                source=resource.source,
            )
        )
    return [
        VersionNode(
            concept_id=revision.concept_id,
            code=code,
            label_zh=revision.label_zh,
            label_en=revision.label_en,
            chapter_code=revision.chapter_code,
            node_type=revision.node_type,
            introduction=revision.introduction,
            learning_focus=revision.learning_focus,
            resources=tuple(grouped.get(revision.concept_id, ())),
        )
        for revision, code in rows
    ]


def list_resource_links(session: Session, version_id: int) -> list[tuple[int, VersionResource]]:
    """扁平资料列表，保留所属概念，供草稿详情按概念展示。"""
    return [
        (
            resource.concept_id,
            VersionResource(
                resource_id=resource.resource_id,
                title=resource.title,
                url=resource.url,
                source=resource.source,
            ),
        )
        for resource in list_resources(session, version_id)
    ]
