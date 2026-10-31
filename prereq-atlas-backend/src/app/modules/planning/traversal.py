"""缺口追溯：按目标反向遍历先修关系，再合并多目标的关联。

追溯只读传入的固定版本快照与冻结输入，不查库、不读当前掌握状态、不访问文件或
网络。每个目标独立收集它需要的未掌握先修，合并时按概念去重但保留全部关联目标，
避免用一个全局 visited 集合把后续目标的解释截断。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.common.contracts import (
    MasteredBoundary,
    PlanningEdge,
    PlanningInput,
    PlanningTarget,
    ReasonCode,
    TargetStatus,
    VersionEdge,
)
from app.modules.planning.contracts import (
    UNKNOWN_TARGET,
    VERSION_MISMATCH,
    GapNode,
    GapTrace,
    PlanningDataError,
    PlanningIssue,
    PrerequisiteGraph,
)

_CANONICAL_REASON_ORDER: tuple[ReasonCode, ...] = (
    ReasonCode.TARGET,
    ReasonCode.PREREQUISITE,
    ReasonCode.MASTERED_BOUNDARY,
)


def trace_gaps(graph: PrerequisiteGraph, planning: PlanningInput) -> GapTrace:
    """追溯全部未掌握目标，返回待复习集合、目标状态、边界与采用依据。"""
    _validate(graph, planning)

    trace = _Trace(
        mastered=frozenset(
            concept_id
            for concept_id in planning.mastered_concept_ids
            if concept_id in graph.node_index
        ),
        edge_by_pair={(edge.source_id, edge.target_id): edge for edge in graph.edges},
    )
    targets: list[PlanningTarget] = []
    for target in dict.fromkeys(planning.target_ids):
        if target in trace.mastered:
            targets.append(PlanningTarget(concept_id=target, status=TargetStatus.MASTERED))
            continue
        targets.append(PlanningTarget(concept_id=target, status=TargetStatus.NEEDS_REVIEW))
        trace.walk(graph, target)

    return GapTrace(
        graph_version_id=graph.version_id,
        content_hash=graph.content_hash,
        algorithm_version=planning.algorithm_version,
        targets=tuple(targets),
        nodes=tuple(trace.node(concept_id) for concept_id in sorted(trace.reasons)),
        dependency_edges=tuple(
            _planning_edge(edge)
            for (source, target), edge in sorted(trace.edge_by_pair.items())
            if source in trace.reasons and target in trace.reasons
        ),
        mastered_boundaries=tuple(
            trace.boundary(concept_id) for concept_id in sorted(trace.boundary_related)
        ),
    )


@dataclass
class _Trace:
    """一次追溯的累计状态；每个目标的 ``seen`` 独立，累计部分只做并集。"""

    # 已掌握的概念 ID 集合
    mastered: frozenset[int]
    # 用 (source_id, target_id) 做键，快速查找对应边
    edge_by_pair: dict[tuple[int, int], VersionEdge]
    # 记录某个节点为什么被纳入追溯，例如是目标节点还是先修节点
    reasons: dict[int, set[ReasonCode]] = field(default_factory=dict)
    # 记录某个节点和哪些目标节点有关
    related: dict[int, set[int]] = field(default_factory=dict)
    # 记录某个节点的直接先修节点
    prerequisites: dict[int, set[int]] = field(default_factory=dict)
    # 记录某个「已掌握边界节点」影响了哪些目标
    boundary_related: dict[int, set[int]] = field(default_factory=dict)
    # 记录边界上涉及的那些边
    boundary_edge_by_pair: dict[tuple[int, int], VersionEdge] = field(default_factory=dict)

    def walk(self, graph: PrerequisiteGraph, target: int) -> None:
        """从目标反向追溯，遇到已掌握节点即停止该分支并记为边界。"""
        self.reasons.setdefault(target, set()).add(ReasonCode.TARGET)
        self.related.setdefault(target, set()).add(target)
        seen = {target}
        stack = [target]
        while stack:
            current = stack.pop()
            for prerequisite in sorted(graph.prerequisites_of(current)):
                self.prerequisites.setdefault(current, set()).add(prerequisite)
                edge = self.edge_by_pair[(prerequisite, current)]
                if prerequisite in self.mastered:
                    self.boundary_related.setdefault(prerequisite, set()).add(target)
                    self.boundary_edge_by_pair.setdefault((prerequisite, current), edge)
                    continue
                self.reasons.setdefault(prerequisite, set()).add(ReasonCode.PREREQUISITE)
                self.related.setdefault(prerequisite, set()).add(target)
                if prerequisite not in seen:
                    seen.add(prerequisite)
                    stack.append(prerequisite)

    def node(self, concept_id: int) -> GapNode:
        return GapNode(
            concept_id=concept_id,
            reason_codes=tuple(
                sorted(self.reasons[concept_id], key=_CANONICAL_REASON_ORDER.index)
            ),
            related_target_ids=tuple(sorted(self.related[concept_id])),
            prerequisite_ids=tuple(sorted(self.prerequisites.get(concept_id, set()))),
        )

    def boundary(self, concept_id: int) -> MasteredBoundary:
        return MasteredBoundary(
            concept_id=concept_id,
            related_target_ids=tuple(sorted(self.boundary_related[concept_id])),
            boundary_edges=tuple(
                _planning_edge(edge)
                for (source, _), edge in sorted(self.boundary_edge_by_pair.items())
                if source == concept_id
            ),
        )


def _planning_edge(edge: VersionEdge) -> PlanningEdge:
    return PlanningEdge(
        source_id=edge.source_id,
        target_id=edge.target_id,
        relation_id=edge.relation_id,
        review_basis=edge.review_basis,
    )


def _validate(graph: PrerequisiteGraph, planning: PlanningInput) -> None:
    """拒绝与固定版本不一致或指向未知概念的输入，不返回半成品。"""
    issues: list[PlanningIssue] = []
    if graph.version_id != planning.graph_version_id:
        issues.append(
            PlanningIssue(
                code=VERSION_MISMATCH,
                message=(
                    f"规划输入固定版本 {planning.graph_version_id}，"
                    f"但依赖图来自版本 {graph.version_id}"
                ),
            )
        )
    unknown = tuple(
        sorted(
            concept_id
            for concept_id in planning.target_ids
            if concept_id not in graph.node_index
        )
    )
    if unknown:
        issues.append(
            PlanningIssue(
                code=UNKNOWN_TARGET,
                concept_ids=unknown,
                message=f"目标概念不在固定版本中 {list(unknown)}",
            )
        )
    if issues:
        raise PlanningDataError(tuple(issues))
