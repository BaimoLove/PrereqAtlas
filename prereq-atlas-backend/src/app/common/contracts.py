"""跨模块共享契约：ID、分页、修订号与时间序列化。

本模块只定义“跨模块真正共享”的结构：业务模块自己的中间对象留在所属包内。
HTTP 字段模型在 :mod:`app.common.http_contracts`，接口目录在
:mod:`app.common.api_catalog`，异常类型与处理器在 T04 接入。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    Strict,
    model_validator,
)

# BIGINT UNSIGNED 上限；HTTP 中的 ID 用十进制字符串表达，避免 JS Number 精度损失。
MAX_BIGINT_UNSIGNED = 2**64 - 1
# JSON 安全整数上限（2^53-1）。revision 不从数据库列读取到 JS 之外，仍按此保守限制。
MAX_SAFE_INTEGER = 2**53 - 1

PAGE_MIN = 1
PAGE_SIZE_MIN = 1
PAGE_SIZE_MAX = 100
PAGE_SIZE_DEFAULT = 20

_ID_PATTERN = re.compile(r"[1-9][0-9]*")


def validate_id_string(value: str) -> str:
    """校验十进制正整数 ID 字符串；不接受符号、前导零、小数或指数形式。"""
    if not _ID_PATTERN.fullmatch(value):
        raise ValueError("ID 必须是正整数的十进制字符串")
    if int(value) > MAX_BIGINT_UNSIGNED:
        raise ValueError("ID 超出 BIGINT UNSIGNED 范围")
    return value


def format_id(value: int) -> str:
    """把内部 int 转为 HTTP 使用的十进制字符串。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("ID 必须是整数")
    if value < 1 or value > MAX_BIGINT_UNSIGNED:
        raise ValueError("ID 超出 BIGINT UNSIGNED 范围")
    return str(value)


def parse_id(value: str) -> int:
    """把 HTTP 十进制字符串转为内部 int；非法值抛 ValueError。"""
    validate_id_string(value)
    return int(value)


def serialize_utc_millis(value: datetime) -> str:
    """按 UTC ISO-8601 毫秒输出；无时区的数据库时间按 UTC 解释。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    value = value.astimezone(UTC)
    return f"{value:%Y-%m-%dT%H:%M:%S}.{value.microsecond // 1000:03d}Z"


# HTTP 请求/响应中的持久化 ID：字符串、严格类型、十进制正整数。
IdStr = Annotated[str, Strict(), AfterValidator(validate_id_string)]

# 乐观锁修订号：非负且不超过 JSON 安全整数。
Revision = Annotated[int, Strict(), Field(ge=0, le=MAX_SAFE_INTEGER)]

# review_plan_node.progress_revision 对应 DDL 的 INT UNSIGNED。
ProgressRevision = Annotated[int, Strict(), Field(ge=0, le=2**32 - 1)]

# 所有对外时间统一 UTC ISO-8601 毫秒格式。
UtcMillis = Annotated[
    datetime, PlainSerializer(serialize_utc_millis, return_type=str, when_used="json")
]


class PageParams(BaseModel):
    """分页查询参数；page/page_size 的边界由 T02 保护值表冻结。"""

    page: Annotated[int, Strict(), Field(ge=PAGE_MIN)] = PAGE_MIN
    page_size: Annotated[int, Strict(), Field(ge=PAGE_SIZE_MIN, le=PAGE_SIZE_MAX)] = (
        PAGE_SIZE_DEFAULT
    )


class Page[ItemT](BaseModel):
    """列表统一返回结构：``{items,total,page,page_size}``。"""

    items: list[ItemT]
    total: Annotated[int, Strict(), Field(ge=0)]
    page: Annotated[int, Strict(), Field(ge=PAGE_MIN)]
    page_size: Annotated[int, Strict(), Field(ge=PAGE_SIZE_MIN, le=PAGE_SIZE_MAX)]


# =============================================================================
# 枚举：取值范围与 schema.sql 的 CHECK、plan 第 5 节一致，不在此新增业务状态。
# =============================================================================


class Role(StrEnum):
    STUDENT = "STUDENT"
    ADMIN = "ADMIN"


class AuthLevel(StrEnum):
    """接口权限级别：P 公开、L 有效登录、S 学生、A 管理员。"""

    PUBLIC = "P"
    AUTHENTICATED = "L"
    STUDENT = "S"
    ADMIN = "A"


class GraphState(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"


class ImportStatus(StrEnum):
    STAGED = "STAGED"
    FAILED = "FAILED"
    CONSUMED = "CONSUMED"


class Necessity(StrEnum):
    REQUIRED = "REQUIRED"
    RECOMMENDED = "RECOMMENDED"
    OPTIONAL = "OPTIONAL"


class ReviewStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ProgressStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


class MasteryVisibility(StrEnum):
    VISIBLE = "VISIBLE"
    NOT_VISIBLE = "NOT_VISIBLE"
    UNKNOWN = "UNKNOWN"


class TargetStatus(StrEnum):
    MASTERED = "MASTERED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class PlanOutcome(StrEnum):
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    NO_REVIEW_NEEDED = "NO_REVIEW_NEEDED"


class ReasonCode(StrEnum):
    TARGET = "TARGET"
    PREREQUISITE = "PREREQUISITE"
    MASTERED_BOUNDARY = "MASTERED_BOUNDARY"


class ErrorCode(StrEnum):
    """统一错误码；HTTP 状态映射见 :data:`ERROR_STATUS`。"""

    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    CONFLICT = "CONFLICT"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"


ERROR_STATUS: dict[ErrorCode, int] = {
    ErrorCode.AUTHENTICATION_REQUIRED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.METHOD_NOT_ALLOWED: 405,
    ErrorCode.CONFLICT: 409,
    ErrorCode.PAYLOAD_TOO_LARGE: 413,
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.SERVICE_UNAVAILABLE: 503,
}


class ErrorResponse(BaseModel):
    """统一错误体：``{code,message,details,request_id}``，不返回堆栈与连接串。"""

    model_config = ConfigDict(extra="forbid")

    code: ErrorCode
    message: str
    details: dict[str, Any] | None = None
    request_id: str


class FieldError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    reason: str


class ReferenceErrorDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_type: str
    ids: list[IdStr]


class ConcurrencyErrorDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: Revision
    actual_revision: Revision


class MissingTargetsDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    missing_target_ids: list[IdStr]


class Actor(BaseModel):
    """认证上下文；只由 identity 模块产生，其他模块不自行解析 token。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: Annotated[int, Strict(), Field(ge=1, le=MAX_BIGINT_UNSIGNED)]
    role: Role
    token_version: Annotated[int, Strict(), Field(ge=0, le=2**32 - 1)]


# =============================================================================
# 保护值：plan 第 5.1 节冻结的阈值。调整阈值必须先改 plan 再复验受影响任务。
# =============================================================================

TARGET_MIN = 1
TARGET_MAX = 20

IMPORT_MAX_BYTES = 10 * 1024 * 1024
IMPORT_MAX_RECORDS = 10000

BATCH_REVIEW_MIN = 1
BATCH_REVIEW_MAX = 112

TITLE_MIN = 1
TITLE_MAX = 100
DEFAULT_PLAN_TITLE = "复习方案"

VERSION_LABEL_MIN = 1
VERSION_LABEL_MAX = 64

CHAPTER_CODE_MIN = 1
CHAPTER_CODE_MAX = 32
CHAPTER_NAME_MIN = 1
CHAPTER_NAME_MAX = 100

CONCEPT_CODE_MIN = 1
CONCEPT_CODE_MAX = 64
SOURCE_NAMESPACE_MAX = 64
SOURCE_KEY_MAX = 64
LABEL_ZH_MIN = 1
LABEL_ZH_MAX = 200
LABEL_EN_MAX = 200
NODE_TYPE_MAX = 32
TEXT_MAX = 10000

URL_MAX = 1000
SOURCE_NOTE_MAX = 255
RATIONALE_MIN = 1
RATIONALE_MAX = 500

USERNAME_MIN = 3
USERNAME_MAX = 32
USERNAME_PATTERN = r"^[A-Za-z0-9_]+$"
PASSWORD_MIN = 8
PASSWORD_MAX = 128

SEARCH_MAX = 100
ACCESS_TOKEN_EXPIRE_MINUTES_DEFAULT = 60


def within_import_byte_limit(size: int) -> bool:
    """单文件大小保护：空文件无效，最大 10 MiB。"""
    return 1 <= size <= IMPORT_MAX_BYTES


def within_import_record_limit(count: int) -> bool:
    """单文件有效记录数保护：空文件无效，最多 10000 条。"""
    return 1 <= count <= IMPORT_MAX_RECORDS


# =============================================================================
# 版本与规划共享结构：M02/M06/M07/M04/M05 之间传递的数据。
# 内部 ID 使用 int；只有 HTTP 层转换为十进制字符串。
# =============================================================================


class InternalModel(BaseModel):
    """内部共享结构：冻结、禁止额外字段。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


InternalId = Annotated[int, Strict(), Field(ge=1, le=MAX_BIGINT_UNSIGNED)]
Sha256Hex = Annotated[str, Strict(), Field(pattern=r"^[0-9a-f]{64}$")]
VersionLabelText = Annotated[str, Strict(), Field(min_length=1, max_length=64)]
AlgorithmVersion = Annotated[str, Strict(), Field(min_length=1, max_length=32)]
StageIndex = Annotated[int, Strict(), Field(ge=1, le=65535)]


class VersionChapter(InternalModel):
    """版本内章节；display_order 是来源建议顺序，不是复习阶段。"""

    chapter_code: Annotated[str, Strict(), Field(min_length=1, max_length=32)]
    name: Annotated[str, Strict(), Field(min_length=1, max_length=100)]
    display_order: Annotated[int, Strict(), Field(ge=0, le=2**32 - 1)]


class VersionResource(InternalModel):
    """版本内资料；方案快照保存副本，不引用本结构。"""

    resource_id: InternalId
    title: Annotated[str, Strict(), Field(min_length=1, max_length=200)]
    url: Annotated[str, Strict(), Field(min_length=1, max_length=1000)]
    source: Annotated[str, Strict(), Field(max_length=200)] | None = None


class VersionNode(InternalModel):
    """一个概念在某固定版本中的完整内容。"""

    concept_id: InternalId
    code: Annotated[str, Strict(), Field(min_length=1, max_length=64)]
    label_zh: Annotated[str, Strict(), Field(min_length=1, max_length=200)]
    label_en: Annotated[str, Strict(), Field(max_length=200)] | None = None
    chapter_code: Annotated[str, Strict(), Field(min_length=1, max_length=32)] | None = None
    node_type: Annotated[str, Strict(), Field(max_length=32)] | None = None
    introduction: Annotated[str, Strict(), Field(max_length=10000)] | None = None
    learning_focus: Annotated[str, Strict(), Field(max_length=10000)] | None = None
    resources: tuple[VersionResource, ...] = ()


class VersionEdge(InternalModel):
    """版本内关系；necessity 与 review_status 决定是否进入规划依赖视图。"""

    relation_id: InternalId
    source_id: InternalId
    target_id: InternalId
    necessity: Necessity
    review_status: ReviewStatus
    structural_type: Annotated[str, Strict(), Field(max_length=16)] | None = None
    review_basis: Annotated[str, Strict(), Field(max_length=500)] | None = None


class VersionData(InternalModel):
    """M07 构图所需的完整版本内容；包含全部节点与全部关系记录。"""

    version_id: InternalId
    content_hash: Sha256Hex | None = None
    chapters: tuple[VersionChapter, ...] = ()
    nodes: tuple[VersionNode, ...] = ()
    edges: tuple[VersionEdge, ...] = ()


class PlanningInput(InternalModel):
    """一次规划的冻结输入；算法内部不得再次读取当前版本或掌握状态。"""

    graph_version_id: InternalId
    target_ids: Annotated[
        tuple[InternalId, ...], Field(min_length=TARGET_MIN, max_length=TARGET_MAX)
    ]
    mastered_concept_ids: tuple[InternalId, ...] = ()
    algorithm_version: AlgorithmVersion


class PlanningTarget(InternalModel):
    concept_id: InternalId
    status: TargetStatus


class PlanningStage(InternalModel):
    stage_index: StageIndex
    concept_ids: Annotated[tuple[InternalId, ...], Field(min_length=1)]


class PlanningNode(InternalModel):
    concept_id: InternalId
    stage_index: StageIndex
    reason_codes: Annotated[tuple[ReasonCode, ...], Field(min_length=1)]
    related_target_ids: tuple[InternalId, ...] = ()
    prerequisite_ids: tuple[InternalId, ...] = ()


class PlanningEdge(InternalModel):
    """待复习节点间的诱导边，或掌握边界到待复习节点的边界边。"""

    source_id: InternalId
    target_id: InternalId
    relation_id: InternalId
    review_basis: Annotated[str, Strict(), Field(max_length=500)] | None = None


class MasteredBoundary(InternalModel):
    """追溯在已掌握节点终止的位置；不全局屏蔽其他分支。"""

    concept_id: InternalId
    related_target_ids: Annotated[tuple[InternalId, ...], Field(min_length=1)]
    boundary_edges: tuple[PlanningEdge, ...] = ()


class PlanningResult(InternalModel):
    """一次规划的完整可解释结果；排序由算法保证稳定。"""

    graph_version_id: InternalId
    graph_content_hash: Sha256Hex
    algorithm_version: AlgorithmVersion
    targets: Annotated[tuple[PlanningTarget, ...], Field(min_length=1)]
    stages: tuple[PlanningStage, ...] = ()
    nodes: tuple[PlanningNode, ...] = ()
    dependency_edges: tuple[PlanningEdge, ...] = ()
    mastered_boundaries: tuple[MasteredBoundary, ...] = ()
    outcome: PlanOutcome

    @model_validator(mode="after")
    def _check_consistency(self) -> PlanningResult:
        target_ids = [target.concept_id for target in self.targets]
        if len(target_ids) != len(set(target_ids)):
            raise ValueError("目标不能重复")

        stage_indexes = [stage.stage_index for stage in self.stages]
        if len(stage_indexes) != len(set(stage_indexes)):
            raise ValueError("阶段序号不能重复")
        if sorted(stage_indexes) != list(range(1, len(stage_indexes) + 1)):
            raise ValueError("阶段序号必须从 1 开始连续")

        node_ids = [node.concept_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("待复习节点不能重复")
        stage_concepts = [concept_id for stage in self.stages for concept_id in stage.concept_ids]
        if len(stage_concepts) != len(set(stage_concepts)):
            raise ValueError("同一概念不能出现在多个阶段")
        if set(stage_concepts) != set(node_ids):
            raise ValueError("阶段集合与待复习节点集合必须一致")

        stage_by_concept = {
            concept_id: stage.stage_index
            for stage in self.stages
            for concept_id in stage.concept_ids
        }
        for node in self.nodes:
            if stage_by_concept.get(node.concept_id) != node.stage_index:
                raise ValueError("节点阶段与所属阶段不一致")

        if self.outcome is PlanOutcome.NO_REVIEW_NEEDED:
            if self.stages or self.nodes or self.dependency_edges or self.mastered_boundaries:
                raise ValueError("无需复习时不得包含阶段、节点、依赖边或掌握边界")
            if any(target.status is TargetStatus.NEEDS_REVIEW for target in self.targets):
                raise ValueError("无需复习时所有目标必须为已掌握")
        else:
            if not self.nodes:
                raise ValueError("需要复习时必须包含待复习节点")
            if all(target.status is TargetStatus.MASTERED for target in self.targets):
                raise ValueError("所有目标已掌握时应返回无需复习")
        return self
