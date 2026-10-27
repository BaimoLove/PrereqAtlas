"""M06 草稿生命周期用例：创建（空草稿或复制发布版）、详情读取与版本列表。

事务由本模块组织：一次调用一个事务，成功前提交；任何异常整体回滚，审计与内容
同生共死，不会留下“假成功”。内容写入全部经 M02 公开面，不直接写 catalog 表。
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.common.contracts import (
    Actor,
    ErrorCode,
    GraphState,
    Necessity,
    ReviewStatus,
    Role,
    parse_id,
)
from app.common.http_contracts import (
    AdminRelation,
    ChapterItem,
    ConceptContentOut,
    CreateDraftRequest,
    DraftCreateResponse,
    DraftDetail,
    DraftResourceItem,
    DraftReviewSummary,
    DraftValidationSummary,
    GraphNode,
    VersionListResponse,
    VersionSummary,
)
from app.infra.errors import AppError
from app.modules.catalog import public as catalog
from app.modules.catalog.public import DraftWriteContext
from app.modules.graph_admin import repository
from app.modules.graph_admin.models import GraphRelation, GraphVersion

CREATE_DRAFT_ACTION = "CREATE_DRAFT"


def ensure_draft_writable(
    session: Session, *, version_id: int, expected_revision: int
) -> None:
    """锁定版本行并核对可写性；catalog 以该函数作为 ``DraftWriteContext.gate``。"""
    version = repository.lock_version(session, version_id)
    if version is None:
        raise AppError(ErrorCode.NOT_FOUND, "图谱版本不存在")
    if version.state != GraphState.DRAFT.value:
        raise AppError(
            ErrorCode.CONFLICT,
            "已发布版本不可编辑",
            {
                "resource_type": "graph_version",
                "ids": [str(version_id)],
                "state": version.state,
            },
        )
    if version.draft_revision != expected_revision:
        raise AppError(
            ErrorCode.CONFLICT,
            "草稿版本号已变化",
            {
                "expected_revision": expected_revision,
                "actual_revision": version.draft_revision,
            },
        )


def _require_admin(actor: Actor) -> None:
    if actor.role is not Role.ADMIN:
        raise AppError(ErrorCode.FORBIDDEN)


def _structural_type(value: str | None) -> Literal["direct", "transitive"] | None:
    """把库中的来源结构分类收窄为契约枚举；其他取值说明数据异常，不静默丢弃。"""
    if value is None:
        return None
    if value == "direct":
        return "direct"
    if value == "transitive":
        return "transitive"
    raise AppError(ErrorCode.INTERNAL_ERROR, "关系的来源结构分类非法", {"value": value})


def _admin_relation(relation: GraphRelation) -> AdminRelation:
    return AdminRelation(
        id=str(relation.id),
        source_concept_id=str(relation.source_concept_id),
        target_concept_id=str(relation.target_concept_id),
        necessity=Necessity(relation.necessity),
        review_status=ReviewStatus(relation.review_status),
        rationale=relation.rationale,
        reviewed_by=str(relation.reviewed_by) if relation.reviewed_by is not None else None,
        reviewed_at=relation.reviewed_at,
        structural_type=_structural_type(relation.structural_type),
        source_edge_key=relation.source_edge_key,
    )


def _draft_detail(session: Session, version: GraphVersion) -> DraftDetail:
    chapters = catalog.read_version_chapters(session, version.id)
    nodes = catalog.read_version_nodes(session, version.id)
    resources = catalog.read_version_resources(session, version.id)
    relations = repository.list_relations(session, version.id)
    counts = repository.review_counts(session, version.id)
    validated = version.validated_revision
    return DraftDetail(
        draft_id=str(version.id),
        version_label=version.version_label,
        state=GraphState(version.state),
        base_version_id=(
            str(version.base_version_id) if version.base_version_id is not None else None
        ),
        draft_revision=version.draft_revision,
        validated_revision=validated,
        chapters=[
            ChapterItem(
                chapter_code=chapter.chapter_code,
                name=chapter.name,
                display_order=chapter.display_order,
            )
            for chapter in chapters
        ],
        concepts=[
            GraphNode(
                concept_id=str(node.concept_id),
                code=node.code,
                content=ConceptContentOut(
                    label_zh=node.label_zh,
                    label_en=node.label_en,
                    chapter_code=node.chapter_code,
                    node_type=node.node_type,
                    introduction=node.introduction,
                    learning_focus=node.learning_focus,
                ),
            )
            for node in nodes
        ],
        relations=[_admin_relation(relation) for relation in relations],
        resources=[
            DraftResourceItem(
                resource_id=str(link.resource.resource_id),
                concept_id=str(link.concept_id),
                title=link.resource.title,
                url=link.resource.url,
                source=link.resource.source,
            )
            for link in resources
        ],
        review_summary=DraftReviewSummary(
            approved_count=counts["APPROVED"],
            rejected_count=counts["REJECTED"],
            pending_count=counts["PENDING"],
        ),
        validation_summary=DraftValidationSummary(
            validated_revision=validated,
            valid=validated is not None and validated == version.draft_revision,
            issue_count=0,
        ),
    )


def create_draft(
    session: Session, *, actor: Actor, request: CreateDraftRequest
) -> DraftCreateResponse:
    """创建空草稿，或从已发布版本复制出完整内容与关系的新草稿。"""
    _require_admin(actor)
    label = request.version_label
    if repository.find_version_by_label(session, label) is not None:
        raise AppError(
            ErrorCode.CONFLICT,
            "版本标签已存在",
            {"resource_type": "graph_version", "label": label},
        )

    base: GraphVersion | None = None
    if request.base_version_id is not None:
        base = repository.get_version(session, parse_id(request.base_version_id))
        if base is None:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "复制来源版本不存在",
                {"resource_type": "graph_version", "ids": [request.base_version_id]},
            )
        if base.state != GraphState.PUBLISHED.value:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "只能从已发布版本复制草稿",
                {
                    "resource_type": "graph_version",
                    "ids": [request.base_version_id],
                    "state": base.state,
                },
            )

    try:
        version = repository.create_version(
            session,
            version_label=label,
            base_version_id=base.id if base is not None else None,
            created_by=actor.user_id,
            draft_revision=1 if base is not None else 0,
        )
    except IntegrityError as exc:
        raise AppError(
            ErrorCode.CONFLICT,
            "版本标签已存在",
            {"resource_type": "graph_version", "label": label},
        ) from exc

    if base is not None:
        context = DraftWriteContext(
            version_id=version.id,
            expected_revision=version.draft_revision,
            actor_id=actor.user_id,
            gate=ensure_draft_writable,
        )
        content = catalog.copy_content(session, base.id, context)
        relation_count = repository.copy_relations(
            session, source_version_id=base.id, target_version_id=version.id
        )
        summary = (
            f"从版本 {base.id} 复制草稿：章节 {content.chapter_count}、"
            f"概念 {content.concept_count}、资料 {content.resource_count}、"
            f"关系 {relation_count}"
        )
    else:
        summary = "创建空草稿"

    repository.add_audit(
        session,
        version_id=version.id,
        operator_id=actor.user_id,
        action=CREATE_DRAFT_ACTION,
        revision_before=None,
        revision_after=version.draft_revision,
        summary=summary,
    )
    session.commit()
    return DraftCreateResponse(
        draft_id=str(version.id), revision=version.draft_revision
    )


def get_draft(session: Session, draft_id: str, *, actor: Actor) -> DraftDetail:
    """读取草稿或已发布版本的完整内容摘要。"""
    _require_admin(actor)
    version = repository.get_version(session, parse_id(draft_id))
    if version is None:
        raise AppError(ErrorCode.NOT_FOUND, "图谱版本不存在")
    return _draft_detail(session, version)


def list_versions(
    session: Session, *, actor: Actor, state: GraphState | None, page: int, page_size: int
) -> VersionListResponse:
    """按状态过滤、创建时间倒序分页；生效标记来自 active_graph 单例。"""
    _require_admin(actor)
    state_value = state.value if state is not None else None
    total = repository.count_versions(session, state=state_value)
    rows = repository.list_versions(
        session, state=state_value, offset=(page - 1) * page_size, limit=page_size
    )
    active = repository.get_active_graph(session)
    items = [
        VersionSummary(
            version_id=str(row.id),
            version_label=row.version_label,
            state=GraphState(row.state),
            content_hash=row.content_hash,
            draft_revision=row.draft_revision,
            validated_revision=row.validated_revision,
            published_at=row.published_at,
            is_active=active is not None and active.version_id == row.id,
            active_revision=(
                active.revision
                if active is not None and active.version_id == row.id
                else None
            ),
        )
        for row in rows
    ]
    return VersionListResponse(items=items, total=total, page=page, page_size=page_size)
