"""M06 图谱管理用例：草稿生命周期、批次接收与关系维护。

事务由本模块组织：一次调用一个事务，成功前提交；任何异常整体回滚，审计与内容
同生共死，不会留下“假成功”。内容写入全部经 M02 公开面，不直接写 catalog 表。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, NoReturn

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.common.contracts import (
    Actor,
    ErrorCode,
    GraphState,
    ImportStatus,
    Necessity,
    ReviewStatus,
    Role,
    parse_id,
)
from app.common.http_contracts import (
    AcceptImportRequest,
    AcceptImportResponse,
    AdminRelation,
    BatchReviewResponse,
    ChapterItem,
    ConceptContentOut,
    CreateDraftRequest,
    DraftCreateResponse,
    DraftDetail,
    DraftResourceItem,
    DraftReviewSummary,
    DraftValidationSummary,
    GraphNode,
    RelationBatchReviewRequest,
    RelationCreateRequest,
    RelationDeleteRequest,
    RelationMutationResponse,
    RelationUpdateRequest,
    RevisionResponse,
    VersionListResponse,
    VersionSummary,
)
from app.common.time import now_beijing
from app.infra.errors import AppError
from app.modules.catalog import public as catalog
from app.modules.catalog.public import ConceptContent, DraftWriteContext
from app.modules.graph_admin import repository
from app.modules.graph_admin.models import GraphRelation, GraphVersion
from app.modules.ingestion import public as ingestion

CREATE_DRAFT_ACTION = "CREATE_DRAFT"
IMPORT_ACTION = "IMPORT"
RELATION_ACTION = "EDIT_RELATION"
DIRECT_EDGE = "direct"
TRANSITIVE_EDGE = "transitive"


def _check_writable(
    version: GraphVersion | None, *, version_id: int, expected_revision: int
) -> GraphVersion:
    """锁行后的可写判定：存在、DRAFT、revision 匹配。"""
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
    return version


def ensure_draft_writable(
    session: Session, *, version_id: int, expected_revision: int
) -> None:
    """锁定版本行并核对可写性；catalog 以该函数作为 ``DraftWriteContext.gate``。"""
    _check_writable(
        repository.lock_version(session, version_id),
        version_id=version_id,
        expected_revision=expected_revision,
    )


def _lock_writable_draft(
    session: Session, version_id: int, expected_revision: int
) -> GraphVersion:
    """与 ``ensure_draft_writable`` 同语义，但返回被锁定的版本行供后续写 revision。"""
    return _check_writable(
        repository.lock_version(session, version_id),
        version_id=version_id,
        expected_revision=expected_revision,
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


@dataclass(frozen=True, slots=True)
class _ConceptCandidate:
    """待写入的一个概念；``source_key`` 同时用作系统内概念编码。"""

    source_key: str
    content: ConceptContent


@dataclass(frozen=True, slots=True)
class _RelationCandidate:
    """待写入的一条采用候选关系；两端用来源节点编号表达，写入时再解析身份。"""

    source_key: str
    target_key: str
    source_edge_key: str
    source_strength: Decimal | None


@dataclass(frozen=True, slots=True)
class _MappingConflict:
    """接收前发现的映射冲突；全部收集后再报错，不静默覆盖已有概念。"""

    kind: str
    source_key: str
    code: str
    existing_concept_id: int | None
    message: str

    def as_details(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "source_key": self.source_key,
            "code": self.code,
            "existing_concept_id": (
                str(self.existing_concept_id) if self.existing_concept_id is not None else None
            ),
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class _ImportPlan:
    """一次接收的完整写入计划；有冲突时调用方一行也不写。"""

    namespace: str
    chapters: tuple[tuple[str, str, int], ...]
    concepts: tuple[_ConceptCandidate, ...]
    relations: tuple[_RelationCandidate, ...]
    transitive_skipped: int
    conflicts: tuple[_MappingConflict, ...]


def _plan_import(
    session: Session, candidates: ingestion.ImportCandidates
) -> _ImportPlan:
    """把批次候选映射为章节/概念/关系写入计划，并收集全部冲突。

    命名空间由节点文件哈希派生：同一份文件重复上传得到同一稳定身份，不同数据集
    不会因 ``N001`` 这类来源编号相同而被误合并。来源节点编号同时作为概念编码，
    编码被其他来源占用时明确报冲突，不做静默覆盖。
    """
    namespace = f"import/{candidates.node_sha256[:16]}"
    conflicts: list[_MappingConflict] = []
    chapters: dict[str, tuple[str, int]] = {}
    concepts: list[_ConceptCandidate] = []
    label_index: dict[str, list[str]] = {}

    for node in candidates.nodes:
        if node.chapter_code:
            if node.chapter_name is None:
                conflicts.append(
                    _MappingConflict(
                        kind="CHAPTER_NAME_MISSING",
                        source_key=node.source_key,
                        code=node.chapter_code,
                        existing_concept_id=None,
                        message="来源章节缺少章节名称",
                    )
                )
            else:
                chapters.setdefault(
                    node.chapter_code, (node.chapter_name, node.chapter_order or 0)
                )

        label = (node.label_zh or "").strip()
        if not label:
            conflicts.append(
                _MappingConflict(
                    kind="LABEL_MISSING",
                    source_key=node.source_key,
                    code=node.source_key,
                    existing_concept_id=None,
                    message="来源节点缺少中文名称",
                )
            )
            continue
        label_index.setdefault(label, []).append(node.source_key)

        code = node.source_key
        by_code = catalog.find_concept_identity(session, code=code)
        by_source = catalog.find_concept_identity(
            session, source_namespace=namespace, source_key=node.source_key
        )
        if by_source is not None and by_source.code != code:
            conflicts.append(
                _MappingConflict(
                    kind="SOURCE_TAKEN",
                    source_key=node.source_key,
                    code=code,
                    existing_concept_id=by_source.concept_id,
                    message="该来源节点已映射到其他概念编码",
                )
            )
            continue
        if by_code is not None and by_code.source_identity != (namespace, node.source_key):
            conflicts.append(
                _MappingConflict(
                    kind="CODE_TAKEN",
                    source_key=node.source_key,
                    code=code,
                    existing_concept_id=by_code.concept_id,
                    message="概念编码已被其他来源身份占用",
                )
            )
            continue

        concepts.append(
            _ConceptCandidate(
                source_key=node.source_key,
                content=ConceptContent(
                    label_zh=label,
                    label_en=node.label_en,
                    chapter_code=node.chapter_code,
                    node_type=node.node_type,
                ),
            )
        )

    for label, source_keys in label_index.items():
        if len(source_keys) < 2:
            continue
        for source_key in source_keys:
            conflicts.append(
                _MappingConflict(
                    kind="DUPLICATE_LABEL",
                    source_key=source_key,
                    code=source_key,
                    existing_concept_id=None,
                    message=f"同一批次内中文名称重复：{label}",
                )
            )

    node_keys = {node.source_key for node in candidates.nodes}
    relations: list[_RelationCandidate] = []
    transitive_skipped = 0
    seen_edges: set[tuple[str, str]] = set()
    for edge in candidates.edges:
        if edge.structural_type == TRANSITIVE_EDGE:
            transitive_skipped += 1
            continue
        if edge.structural_type != DIRECT_EDGE:
            continue
        if edge.source_node_key == edge.target_node_key:
            conflicts.append(
                _MappingConflict(
                    kind="SELF_LOOP",
                    source_key=edge.source_edge_key,
                    code=edge.source_node_key,
                    existing_concept_id=None,
                    message="关系两端是同一概念",
                )
            )
            continue
        missing = [
            key
            for key in (edge.source_node_key, edge.target_node_key)
            if key not in node_keys
        ]
        if missing:
            conflicts.append(
                _MappingConflict(
                    kind="ENDPOINT_MISSING",
                    source_key=edge.source_edge_key,
                    code=edge.source_node_key,
                    existing_concept_id=None,
                    message=f"关系端点不存在：{', '.join(missing)}",
                )
            )
            continue
        pair = (edge.source_node_key, edge.target_node_key)
        if pair in seen_edges:
            conflicts.append(
                _MappingConflict(
                    kind="DUPLICATE_EDGE",
                    source_key=edge.source_edge_key,
                    code=edge.source_node_key,
                    existing_concept_id=None,
                    message="同一批次内重复的先修关系",
                )
            )
            continue
        seen_edges.add(pair)
        relations.append(
            _RelationCandidate(
                source_key=edge.source_node_key,
                target_key=edge.target_node_key,
                source_edge_key=edge.source_edge_key,
                source_strength=edge.source_strength,
            )
        )

    return _ImportPlan(
        namespace=namespace,
        chapters=tuple(
            (chapter_code, name, order)
            for chapter_code, (name, order) in chapters.items()
        ),
        concepts=tuple(concepts),
        relations=tuple(relations),
        transitive_skipped=transitive_skipped,
        conflicts=tuple(conflicts),
    )


def accept_import(
    session: Session,
    draft_id: str,
    *,
    actor: Actor,
    request: AcceptImportRequest,
) -> AcceptImportResponse:
    """把 STAGED 批次的候选写入草稿，并把批次标记为 CONSUMED（同一事务）。"""
    _require_admin(actor)
    version = _lock_writable_draft(
        session, parse_id(draft_id), request.expected_revision
    )
    batch_id = parse_id(request.batch_id)
    candidates = ingestion.read_candidates(session, batch_id)
    if candidates is None:
        raise AppError(ErrorCode.NOT_FOUND, "导入批次不存在")
    if candidates.status == ImportStatus.CONSUMED.value:
        raise AppError(
            ErrorCode.CONFLICT,
            "该批次已被草稿接收",
            {
                "resource_type": "import_batch",
                "ids": [request.batch_id],
                "status": candidates.status,
            },
        )
    if candidates.status != ImportStatus.STAGED.value:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "只有已暂存批次可以被接收",
            {
                "resource_type": "import_batch",
                "ids": [request.batch_id],
                "status": candidates.status,
            },
        )

    plan = _plan_import(session, candidates)
    if plan.conflicts:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "导入数据与现有图谱冲突",
            {"conflicts": [conflict.as_details() for conflict in plan.conflicts]},
        )

    context = DraftWriteContext(
        version_id=version.id,
        expected_revision=request.expected_revision,
        actor_id=actor.user_id,
        gate=ensure_draft_writable,
    )
    for chapter_code, name, display_order in plan.chapters:
        catalog.write_chapter(
            session,
            context,
            chapter_code=chapter_code,
            name=name,
            display_order=display_order,
        )

    mapping: dict[str, int] = {}
    for concept in plan.concepts:
        concept_id = catalog.ensure_concept(
            session,
            context,
            code=concept.source_key,
            source_namespace=plan.namespace,
            source_key=concept.source_key,
        )
        catalog.write_concept_content(
            session, context, concept_id=concept_id, content=concept.content
        )
        mapping[concept.source_key] = concept_id

    try:
        repository.add_relations(
            session,
            version_id=version.id,
            relations=[
                repository.NewRelation(
                    source_concept_id=mapping[relation.source_key],
                    target_concept_id=mapping[relation.target_key],
                    necessity=Necessity.REQUIRED.value,
                    structural_type=DIRECT_EDGE,
                    source_strength=relation.source_strength,
                    review_status=ReviewStatus.PENDING.value,
                    source_batch_id=batch_id,
                    source_edge_key=relation.source_edge_key,
                )
                for relation in plan.relations
            ],
        )
    except IntegrityError as exc:
        raise AppError(
            ErrorCode.CONFLICT,
            "先修关系在该版本内已存在",
            {"resource_type": "graph_relation", "ids": []},
        ) from exc

    ingestion.record_node_mapping(session, batch_id, mapping)
    summary: dict[str, object] = {
        "batch_id": request.batch_id,
        "draft_id": str(version.id),
        "source_namespace": plan.namespace,
        "chapter_count": len(plan.chapters),
        "concept_count": len(mapping),
        "relation_count": len(plan.relations),
        "transitive_skipped": plan.transitive_skipped,
        "mapped_node_count": len(mapping),
        "conflicts": [],
    }
    ingestion.mark_consumed(session, batch_id, summary=summary)

    version.draft_revision = request.expected_revision + 1
    version.validated_revision = None
    repository.add_audit(
        session,
        version_id=version.id,
        operator_id=actor.user_id,
        action=IMPORT_ACTION,
        revision_before=request.expected_revision,
        revision_after=version.draft_revision,
        summary=(
            f"接收批次 {request.batch_id}：章节 {len(plan.chapters)}、"
            f"概念 {len(mapping)}、PENDING 关系 {len(plan.relations)}"
        ),
    )
    session.commit()
    return AcceptImportResponse(
        mapping_summary=summary, new_revision=version.draft_revision
    )


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
