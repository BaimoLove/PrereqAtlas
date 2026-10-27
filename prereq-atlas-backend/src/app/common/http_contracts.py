"""HTTP 字段契约：请求/响应的 Pydantic 模型。

字段名、长度、枚举与保护值以 ``plan.md`` 第 5.3/5.4 节和
:mod:`app.common.contracts` 的冻结值为准。所有写请求模型禁止额外字段；
内部 ID 在 JSON 中一律使用十进制字符串，响应时间使用 UTC+8 毫秒格式。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.common.contracts import (
    BATCH_REVIEW_MAX,
    BATCH_REVIEW_MIN,
    CHAPTER_CODE_MAX,
    CHAPTER_NAME_MAX,
    CONCEPT_CODE_MAX,
    LABEL_EN_MAX,
    LABEL_ZH_MAX,
    NODE_TYPE_MAX,
    PAGE_MIN,
    PAGE_SIZE_DEFAULT,
    PAGE_SIZE_MAX,
    PAGE_SIZE_MIN,
    PASSWORD_MAX,
    PASSWORD_MIN,
    RATIONALE_MAX,
    RATIONALE_MIN,
    SEARCH_MAX,
    SOURCE_KEY_MAX,
    SOURCE_NAMESPACE_MAX,
    SOURCE_NOTE_MAX,
    TARGET_MAX,
    TARGET_MIN,
    TEXT_MAX,
    TITLE_MAX,
    TITLE_MIN,
    URL_MAX,
    USERNAME_MAX,
    USERNAME_MIN,
    USERNAME_PATTERN,
    VERSION_LABEL_MAX,
    VERSION_LABEL_MIN,
    BeijingMillis,
    GraphState,
    IdStr,
    ImportStatus,
    MasteryVisibility,
    Necessity,
    Page,
    ProgressRevision,
    ProgressStatus,
    ReviewStatus,
    Revision,
    Role,
)


class RequestModel(BaseModel):
    """所有写请求的基类：禁止客户端多传越界字段。"""

    model_config = ConfigDict(extra="forbid")


class ResponseModel(BaseModel):
    """响应模型同样冻结字段，避免无意中把内部字段暴露出去。"""

    model_config = ConfigDict(extra="forbid")


# =============================================================================
# 字符串与编码
# =============================================================================


def _validate_code(value: str, *, max_length: int) -> str:
    if not value or len(value) > max_length:
        raise ValueError("编码长度不合法")
    if value != value.strip() or any(ch.isspace() for ch in value):
        raise ValueError("编码不能包含空白")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError("编码不能包含控制字符")
    if "/" in value or "\\" in value or value in {".", ".."}:
        raise ValueError("编码不能包含路径片段")
    return value


def _validate_source_namespace(value: str) -> str:
    # 来源命名空间允许 "/"（如 AL-CPL/data_mining），但不接受空白、控制字符或纯路径片段。
    if not value or len(value) > SOURCE_NAMESPACE_MAX:
        raise ValueError("来源命名空间长度不合法")
    if value != value.strip() or any(ch.isspace() for ch in value):
        raise ValueError("来源命名空间不能包含空白")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError("来源命名空间不能包含控制字符")
    if "\\" in value or value in {".", ".."} or ".." in value.split("/"):
        raise ValueError("来源命名空间不能包含路径片段")
    return value


def _validate_http_url(value: str) -> str:
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError("URL 不能包含空白或控制字符")
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"}:
        raise ValueError("URL 只允许 http 或 https")
    if not parts.hostname:
        raise ValueError("URL 必须有主机名")
    return value


ChapterCode = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=CHAPTER_CODE_MAX),
    AfterValidator(lambda value: _validate_code(value, max_length=CHAPTER_CODE_MAX)),
]

ConceptCode = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=CONCEPT_CODE_MAX),
    AfterValidator(lambda value: _validate_code(value, max_length=CONCEPT_CODE_MAX)),
]

SourceKey = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=SOURCE_KEY_MAX),
    AfterValidator(lambda value: _validate_code(value, max_length=SOURCE_KEY_MAX)),
]

SourceNamespace = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=SOURCE_NAMESPACE_MAX),
    AfterValidator(_validate_source_namespace),
]

Username = Annotated[
    str,
    StringConstraints(
        strict=True, min_length=USERNAME_MIN, max_length=USERNAME_MAX, pattern=USERNAME_PATTERN
    ),
]

Password = Annotated[
    str, StringConstraints(strict=True, min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)
]

Title = Annotated[
    str,
    StringConstraints(
        strict=True, strip_whitespace=True, min_length=TITLE_MIN, max_length=TITLE_MAX
    ),
]

VersionLabel = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=VERSION_LABEL_MIN,
        max_length=VERSION_LABEL_MAX,
    ),
]

ChapterName = Annotated[
    str,
    StringConstraints(
        strict=True, strip_whitespace=True, min_length=1, max_length=CHAPTER_NAME_MAX
    ),
]

LabelZh = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=LABEL_ZH_MAX),
]

Rationale = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=RATIONALE_MIN,
        max_length=RATIONALE_MAX,
    ),
]

OptionalLabelEn = Annotated[str, StringConstraints(strict=True, max_length=LABEL_EN_MAX)]
OptionalNodeType = Annotated[str, StringConstraints(strict=True, max_length=NODE_TYPE_MAX)]
OptionalText = Annotated[str, StringConstraints(strict=True, max_length=TEXT_MAX)]
OptionalUrl = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=URL_MAX),
    AfterValidator(_validate_http_url),
]
OptionalSourceNote = Annotated[str, StringConstraints(strict=True, max_length=SOURCE_NOTE_MAX)]
ResourceTitle = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200)
]
ResourceSource = Annotated[str, StringConstraints(strict=True, max_length=200)]
SearchTerm = Annotated[str, StringConstraints(strict=True, max_length=SEARCH_MAX)]
Sha256Hex = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]

DisplayOrder = Annotated[int, Field(ge=0, le=2**32 - 1)]
PageNumber = Annotated[int, Field(ge=PAGE_MIN)]
PageSize = Annotated[int, Field(ge=PAGE_SIZE_MIN, le=PAGE_SIZE_MAX)]
BeijingMillisOut = BeijingMillis


class ReviewDecision(StrEnum):
    """单条/批量审核只能给出明确结论，不能借此改回 PENDING。"""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


# =============================================================================
# 请求
# =============================================================================


class RegisterRequest(RequestModel):
    username: Username
    password: Password


class TokenForm(RequestModel):
    username: Username
    password: Password


class CreateDraftRequest(RequestModel):
    version_label: VersionLabel
    base_version_id: IdStr | None = None


class DraftWriteRequest(RequestModel):
    expected_revision: Revision


class ChapterSaveRequest(DraftWriteRequest):
    name: ChapterName
    display_order: DisplayOrder = 0


class ChapterDeleteRequest(DraftWriteRequest):
    pass


class ConceptWrite(RequestModel):
    label_zh: LabelZh
    label_en: OptionalLabelEn | None = None
    chapter_code: ChapterCode | None = None
    node_type: OptionalNodeType | None = None
    introduction: OptionalText | None = None
    learning_focus: OptionalText | None = None


class ConceptCreateRequest(ConceptWrite):
    code: ConceptCode
    source_namespace: SourceNamespace | None = None
    source_key: SourceKey | None = None
    expected_revision: Revision

    @model_validator(mode="after")
    def _check_source_pair(self) -> ConceptCreateRequest:
        if (self.source_namespace is None) != (self.source_key is None):
            raise ValueError("source_namespace 与 source_key 必须同时提供或同时缺省")
        return self


class ConceptUpdateRequest(ConceptWrite):
    expected_revision: Revision


class ConceptDeleteRequest(DraftWriteRequest):
    pass


class ResourceCreateRequest(DraftWriteRequest):
    concept_id: IdStr
    title: ResourceTitle
    url: OptionalUrl
    source: ResourceSource | None = None


class ResourceUpdateRequest(DraftWriteRequest):
    concept_id: IdStr
    title: ResourceTitle
    url: OptionalUrl
    source: ResourceSource | None = None


class ResourceDeleteRequest(DraftWriteRequest):
    pass


class RelationCreateRequest(DraftWriteRequest):
    source_concept_id: IdStr
    target_concept_id: IdStr
    necessity: Literal["REQUIRED"] = "REQUIRED"
    rationale: Rationale | None = None

    @model_validator(mode="after")
    def _check_endpoints(self) -> RelationCreateRequest:
        if self.source_concept_id == self.target_concept_id:
            raise ValueError("关系两端不能是同一概念")
        return self


class RelationUpdateRequest(DraftWriteRequest):
    source_concept_id: IdStr | None = None
    target_concept_id: IdStr | None = None
    necessity: Literal["REQUIRED"] | None = None
    review_status: ReviewDecision | None = None
    rationale: Rationale | None = None

    @model_validator(mode="after")
    def _check_change_set(self) -> RelationUpdateRequest:
        changed = (
            self.source_concept_id,
            self.target_concept_id,
            self.necessity,
            self.review_status,
            self.rationale,
        )
        if all(value is None for value in changed):
            raise ValueError("至少提供一个允许变更的字段")
        endpoint_changed = any(
            value is not None
            for value in (self.source_concept_id, self.target_concept_id, self.necessity)
        )
        if self.review_status is not None:
            if endpoint_changed:
                raise ValueError("端点/必要性与审核结论不能在同次请求混改")
            if self.rationale is None:
                raise ValueError("审核结论必须提供理由")
        if (
            self.source_concept_id is not None
            and self.target_concept_id is not None
            and self.source_concept_id == self.target_concept_id
        ):
            raise ValueError("关系两端不能是同一概念")
        return self


class RelationBatchReviewRequest(DraftWriteRequest):
    relation_ids: Annotated[
        list[IdStr], Field(min_length=BATCH_REVIEW_MIN, max_length=BATCH_REVIEW_MAX)
    ]
    review_status: ReviewDecision
    rationale: Rationale


class RelationDeleteRequest(DraftWriteRequest):
    pass


class AcceptImportRequest(DraftWriteRequest):
    batch_id: IdStr


class ValidateDraftRequest(DraftWriteRequest):
    pass


class PublishDraftRequest(DraftWriteRequest):
    expected_active_revision: Revision


class PlanCreateRequest(RequestModel):
    target_ids: Annotated[list[IdStr], Field(min_length=TARGET_MIN, max_length=TARGET_MAX)]
    title: Title | None = None
    request_id: UUID


class ReplanRequest(RequestModel):
    request_id: UUID
    title: Title | None = None


class ProgressUpdateRequest(RequestModel):
    status: ProgressStatus
    expected_revision: ProgressRevision


class PageQuery(RequestModel):
    page: PageNumber = PAGE_MIN
    page_size: PageSize = PAGE_SIZE_DEFAULT


class ConceptSearchQuery(PageQuery):
    version_id: IdStr
    q: SearchTerm | None = None
    chapter: ChapterCode | None = None


class ConceptDetailQuery(RequestModel):
    version_id: IdStr


class VersionListQuery(PageQuery):
    state: GraphState | None = None


class ImportDetailQuery(PageQuery):
    pass


# =============================================================================
# 响应
# =============================================================================


class HealthLiveResponse(ResponseModel):
    status: Literal["ok"] = "ok"


class HealthReadyResponse(ResponseModel):
    status: Literal["ready"] = "ready"
    database: Literal["ok"] = "ok"


class UserSummary(ResponseModel):
    id: IdStr
    username: str
    role: Role


class TokenResponse(ResponseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: Annotated[int, Field(gt=0)]


class ActiveGraphSummary(ResponseModel):
    version_id: IdStr
    label: str
    content_hash: Sha256Hex
    active_revision: Revision


class ChapterItem(ResponseModel):
    chapter_code: ChapterCode
    name: Annotated[str, StringConstraints(max_length=CHAPTER_NAME_MAX)]
    display_order: DisplayOrder


class ConceptContentOut(ResponseModel):
    label_zh: Annotated[str, StringConstraints(max_length=LABEL_ZH_MAX)]
    label_en: OptionalLabelEn | None = None
    chapter_code: ChapterCode | None = None
    node_type: OptionalNodeType | None = None
    introduction: OptionalText | None = None
    learning_focus: OptionalText | None = None


class GraphNode(ResponseModel):
    concept_id: IdStr
    code: Annotated[str, StringConstraints(max_length=CONCEPT_CODE_MAX)]
    content: ConceptContentOut


class GraphEdge(ResponseModel):
    relation_id: IdStr
    source_id: IdStr
    target_id: IdStr
    necessity: Necessity


class GraphResponse(ResponseModel):
    version_id: IdStr
    content_hash: Sha256Hex
    chapters: list[ChapterItem]
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class ResourceItem(ResponseModel):
    resource_id: IdStr
    title: ResourceTitle
    url: OptionalUrl
    source: ResourceSource | None = None


class RelatedConcept(ResponseModel):
    relation_id: IdStr
    concept_id: IdStr
    code: Annotated[str, StringConstraints(max_length=CONCEPT_CODE_MAX)]
    label_zh: Annotated[str, StringConstraints(max_length=LABEL_ZH_MAX)]
    necessity: Necessity


class ConceptCard(ResponseModel):
    version_id: IdStr
    concept_id: IdStr
    code: Annotated[str, StringConstraints(max_length=CONCEPT_CODE_MAX)]
    content: ConceptContentOut
    resources: list[ResourceItem]
    prerequisites: list[RelatedConcept]
    successors: list[RelatedConcept]


class MasteryItem(ResponseModel):
    concept_id: IdStr
    declared_at: BeijingMillisOut
    declared_version_id: IdStr
    visibility: MasteryVisibility


class ProgressItem(ResponseModel):
    concept_id: IdStr
    stage_index: Annotated[int, Field(ge=1)]
    status: ProgressStatus
    revision: ProgressRevision
    updated_at: BeijingMillisOut


class PlanDetail(ResponseModel):
    id: IdStr
    title: Annotated[str, StringConstraints(max_length=TITLE_MAX)]
    owner_id: IdStr
    graph_version_id: IdStr
    schema_version: str
    algorithm_version: str
    based_on_plan_id: IdStr | None = None
    created_at: BeijingMillisOut
    input_snapshot: dict[str, Any]
    result_snapshot: dict[str, Any]
    progress: list[ProgressItem]


class PlanListItem(ResponseModel):
    id: IdStr
    title: Annotated[str, StringConstraints(max_length=TITLE_MAX)]
    graph_version_id: IdStr
    created_at: BeijingMillisOut
    target_count: Annotated[int, Field(ge=0)]
    node_count: Annotated[int, Field(ge=0)]
    completed_count: Annotated[int, Field(ge=0)]
    based_on_plan_id: IdStr | None = None


class PlanListResponse(Page[PlanListItem]):
    pass


class MasteryListResponse(Page[MasteryItem]):
    pass


class ProgressUpdateResponse(ResponseModel):
    status: ProgressStatus
    new_revision: ProgressRevision


class ImportIssue(ResponseModel):
    file_kind: str
    logical_record_no: Annotated[int, Field(ge=1)]
    source_key: Annotated[str, StringConstraints(max_length=SOURCE_KEY_MAX)] | None = None
    code: str
    message: str


class ImportRawItem(ResponseModel):
    """导入明细的一条原始记录；按节点后边、逻辑记录号稳定排序。"""

    file_kind: Literal["node", "edge"]
    source_key: Annotated[str, StringConstraints(max_length=SOURCE_KEY_MAX)]
    logical_record_no: Annotated[int, Field(ge=1)]
    raw: dict[str, Any]


class ImportReport(ResponseModel):
    batch_id: IdStr
    status: ImportStatus
    node_sha256: Sha256Hex
    edge_sha256: Sha256Hex
    source_note: OptionalSourceNote | None = None
    counts: dict[str, int]
    errors: list[ImportIssue]
    warnings: list[ImportIssue]
    mapping_summary: dict[str, Any]
    items: list[ImportRawItem] = Field(default_factory=list)
    page: Annotated[int, Field(ge=1)] = 1
    page_size: Annotated[int, Field(ge=1, le=PAGE_SIZE_MAX)] = PAGE_SIZE_DEFAULT
    total: Annotated[int, Field(ge=0)] = 0


class VersionSummary(ResponseModel):
    version_id: IdStr
    version_label: Annotated[str, StringConstraints(max_length=VERSION_LABEL_MAX)]
    state: GraphState
    content_hash: Sha256Hex | None = None
    draft_revision: Revision
    validated_revision: Revision | None = None
    published_at: BeijingMillisOut | None = None
    is_active: bool
    active_revision: Revision | None = None


class VersionListResponse(Page[VersionSummary]):
    pass


class DraftCreateResponse(ResponseModel):
    draft_id: IdStr
    revision: Revision


class AdminRelation(ResponseModel):
    id: IdStr
    source_concept_id: IdStr
    target_concept_id: IdStr
    necessity: Necessity
    review_status: ReviewStatus
    rationale: Rationale | None = None
    reviewed_by: IdStr | None = None
    reviewed_at: BeijingMillisOut | None = None
    structural_type: Literal["direct", "transitive"] | None = None
    source_edge_key: Annotated[str, StringConstraints(max_length=SOURCE_KEY_MAX)] | None = None

    @model_validator(mode="after")
    def _check_review_record(self) -> AdminRelation:
        if self.review_status is not ReviewStatus.PENDING and (
            self.reviewed_by is None or self.reviewed_at is None
        ):
            raise ValueError("已审核关系必须带审核人与审核时间")
        return self


class DraftReviewSummary(ResponseModel):
    approved_count: Annotated[int, Field(ge=0)]
    rejected_count: Annotated[int, Field(ge=0)]
    pending_count: Annotated[int, Field(ge=0)]


class DraftValidationSummary(ResponseModel):
    validated_revision: Revision | None = None
    valid: bool
    issue_count: Annotated[int, Field(ge=0)]


class DraftDetail(ResponseModel):
    draft_id: IdStr
    version_label: Annotated[str, StringConstraints(max_length=VERSION_LABEL_MAX)]
    state: GraphState
    base_version_id: IdStr | None = None
    draft_revision: Revision
    validated_revision: Revision | None = None
    chapters: list[ChapterItem]
    concepts: list[GraphNode]
    relations: list[AdminRelation]
    resources: list[ResourceItem]
    review_summary: DraftReviewSummary
    validation_summary: DraftValidationSummary


class AcceptImportResponse(ResponseModel):
    mapping_summary: dict[str, Any]
    new_revision: Revision


class RelationMutationResponse(ResponseModel):
    relation: AdminRelation
    new_revision: Revision


class BatchReviewResponse(ResponseModel):
    affected_count: Annotated[int, Field(ge=0)]
    new_revision: Revision


class ChapterMutationResponse(ResponseModel):
    chapter: ChapterItem
    new_revision: Revision


class ConceptMutationResponse(ResponseModel):
    concept: GraphNode
    new_revision: Revision


class ResourceMutationResponse(ResponseModel):
    resource: ResourceItem
    new_revision: Revision


class RevisionResponse(ResponseModel):
    new_revision: Revision


class ValidationIssue(ResponseModel):
    code: str
    concept_ids: list[IdStr]
    relation_ids: list[IdStr]
    message: str


class ValidationReport(ResponseModel):
    draft_id: IdStr
    revision: Revision
    valid: bool
    issues: list[ValidationIssue]
    approved_count: Annotated[int, Field(ge=0)]
    rejected_count: Annotated[int, Field(ge=0)]
    pending_count: Annotated[int, Field(ge=0)]
    cycle_paths: list[list[IdStr]]


class PublishResponse(ResponseModel):
    version_id: IdStr
    published: bool
    runtime_ready: bool
    active_revision: Revision


class AuditItem(ResponseModel):
    version_id: IdStr
    operator_id: IdStr
    action: Annotated[str, StringConstraints(max_length=48)]
    revision_before: Revision | None = None
    revision_after: Revision | None = None
    summary: Annotated[str, StringConstraints(max_length=500)]
    created_at: BeijingMillisOut


class AuditListResponse(Page[AuditItem]):
    pass
