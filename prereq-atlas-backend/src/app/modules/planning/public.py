"""M04 规划模块的公开面：缺口追溯与多目标合并。

只暴露 T17 的计算入口与拒绝类型；分阶段与规划结果由 T18 的引擎在此之上组装。
"""

from app.modules.planning.contracts import (
    UNKNOWN_TARGET,
    VERSION_MISMATCH,
    GapNode,
    GapTrace,
    PlanningDataError,
    PlanningIssue,
    PrerequisiteGraph,
)
from app.modules.planning.traversal import trace_gaps

__all__ = [
    "UNKNOWN_TARGET",
    "VERSION_MISMATCH",
    "GapNode",
    "GapTrace",
    "PlanningDataError",
    "PlanningIssue",
    "PrerequisiteGraph",
    "trace_gaps",
]
