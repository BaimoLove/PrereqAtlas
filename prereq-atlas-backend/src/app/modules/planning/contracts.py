"""M04 规划模块的契约：缺口追溯的中间结果与拒绝码。

追溯只接收两个冻结输入：一个固定版本的只读图，以及一次规划的冻结输入。为了
让 ``planning`` 保持纯计算模块（架构检查禁止它导入其他业务模块），这里用结构化
``Protocol`` 描述图快照的最小只读面，由 M07 的 ``GraphSnapshot`` 在调用处直接满足。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from app.common.contracts import (
    MasteredBoundary,
    PlanningEdge,
    PlanningTarget,
    ReasonCode,
    VersionEdge,
    VersionNode,
)

UNKNOWN_TARGET = "UNKNOWN_TARGET"
VERSION_MISMATCH = "VERSION_MISMATCH"


class PrerequisiteGraph(Protocol):
    """固定版本依赖图的最小只读面；结构化匹配 M07 的快照。"""

    version_id: int
    content_hash: str | None
    node_index: Mapping[int, VersionNode]
    edges: tuple[VersionEdge, ...]

    def prerequisites_of(self, concept_id: int) -> tuple[int, ...]: ...


@dataclass(frozen=True, slots=True)
class PlanningIssue:
    """一个阻止追溯的输入问题；定位到具体概念。"""

    code: str
    concept_ids: tuple[int, ...] = ()
    message: str = ""


class PlanningDataError(Exception):
    """规划输入无法与固定版本图组合；一次收集全部问题。"""

    def __init__(self, issues: tuple[PlanningIssue, ...]) -> None:
        self.issues = issues
        super().__init__("；".join(issue.message for issue in issues) or "规划输入不合法")


@dataclass(frozen=True, slots=True)
class GapNode:
    """一个待复习节点及其解释依据；阶段由 T18 的引擎补充。"""

    concept_id: int
    reason_codes: tuple[ReasonCode, ...]
    related_target_ids: tuple[int, ...]
    prerequisite_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class GapTrace:
    """一次缺口追溯的完整结果：待复习集合、目标状态、边界与采用依据。

    排序由追溯本身保证稳定（概念、目标、关系均按 ID 升序），下游引擎可以直接
    在此基础上分阶段，不重复遍历图。
    """

    graph_version_id: int
    content_hash: str | None
    algorithm_version: str
    targets: tuple[PlanningTarget, ...]
    nodes: tuple[GapNode, ...]
    dependency_edges: tuple[PlanningEdge, ...]
    mastered_boundaries: tuple[MasteredBoundary, ...]
