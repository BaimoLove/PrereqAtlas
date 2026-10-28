"""M02 草稿写入上下文与内容载体。

catalog 不得反向依赖 graph_admin（架构检查禁止该边），因此“版本已锁定、状态为
DRAFT、revision 匹配”由 M06 通过 :class:`DraftStateGate` 注入。catalog 每次写入前
先调用 gate，gate 抛错则本次不产生任何行；catalog 没有 HTTP 入口，客户端无法自造
可写的上下文。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from app.common.contracts import VersionResource


class DraftStateGate(Protocol):
    """M06 提供的“草稿此刻可写”校验；不可写时抛 ``AppError``。"""

    def __call__(self, session: Session, *, version_id: int, expected_revision: int) -> None: ...


@dataclass(frozen=True, slots=True)
class DraftWriteContext:
    """一次草稿写入的上下文：目标版本、期望 revision 与实际校验器。"""

    version_id: int
    expected_revision: int
    actor_id: int
    gate: DraftStateGate

    def __post_init__(self) -> None:
        if self.version_id < 1:
            raise ValueError("version_id 必须是正整数")
        if self.expected_revision < 0:
            raise ValueError("expected_revision 必须是非负整数")
        if self.actor_id < 1:
            raise ValueError("actor_id 必须是正整数")

    def ensure_writable(self, tx: Session) -> None:
        """写入前调用：由 M06 校验草稿状态与 revision。"""
        self.gate(tx, version_id=self.version_id, expected_revision=self.expected_revision)


@dataclass(frozen=True, slots=True)
class ConceptContent:
    """概念在某一版本中的显示内容；``label_zh`` 是唯一必填字段。"""

    label_zh: str
    label_en: str | None = None
    chapter_code: str | None = None
    node_type: str | None = None
    introduction: str | None = None
    learning_focus: str | None = None


@dataclass(frozen=True, slots=True)
class CopyContentResult:
    """版本复制写入的行数统计，供 M06 生成报告与审计摘要。"""

    chapter_count: int
    concept_count: int
    resource_count: int


@dataclass(frozen=True, slots=True)
class ConceptIdentity:
    """概念稳定身份的只读视图，供调用方在写入前核对映射冲突。"""

    concept_id: int
    code: str
    source_namespace: str | None
    source_key: str | None

    @property
    def source_identity(self) -> tuple[str | None, str | None]:
        return (self.source_namespace, self.source_key)


@dataclass(frozen=True, slots=True)
class VersionResourceLink:
    """资料及其所属概念；草稿详情需要按概念展示资料。"""

    concept_id: int
    resource: VersionResource
