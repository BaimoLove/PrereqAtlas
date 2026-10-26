"""P0 冻结的图谱夹具：精确输入与精确期望。

这些夹具是后续 T13/T17/T18 的验收基线，不是演示数据，也不是导入批次。
期望值以字面量写明，测试只检查夹具自身与契约的一致性，不实现规划算法。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

from app.common.contracts import (
    Necessity,
    PlanningInput,
    PlanOutcome,
    ReasonCode,
    ReviewStatus,
    TargetStatus,
    VersionChapter,
    VersionData,
    VersionEdge,
    VersionNode,
)

_APPROVED = ReviewStatus.APPROVED
_REQUIRED = Necessity.REQUIRED


def _content_hash(version_id: int) -> str:
    return hashlib.sha256(f"fixture-{version_id}".encode()).hexdigest()


def _node(concept_id: int, code: str, label_zh: str, chapter_code: str = "M01") -> VersionNode:
    return VersionNode(
        concept_id=concept_id,
        code=code,
        label_zh=label_zh,
        chapter_code=chapter_code,
    )


def _edge(
    relation_id: int,
    source_id: int,
    target_id: int,
    *,
    review_status: ReviewStatus = _APPROVED,
    necessity: Necessity = _REQUIRED,
) -> VersionEdge:
    return VersionEdge(
        relation_id=relation_id,
        source_id=source_id,
        target_id=target_id,
        necessity=necessity,
        review_status=review_status,
    )


def _version(version_id: int, nodes: list[VersionNode], edges: list[VersionEdge]) -> VersionData:
    return VersionData(
        version_id=version_id,
        content_hash=_content_hash(version_id),
        chapters=(
            VersionChapter(chapter_code="M01", name="统计基础", display_order=1),
        ),
        nodes=tuple(nodes),
        edges=tuple(edges),
    )


@dataclass(frozen=True)
class Scenario:
    name: str
    version: VersionData
    planning_input: PlanningInput
    expected_review: tuple[int, ...]
    expected_stages: tuple[tuple[int, ...], ...]
    expected_target_status: Mapping[int, TargetStatus]
    expected_boundaries: tuple[int, ...]
    expected_boundary_edges: tuple[tuple[int, int], ...]
    expected_dependency_edges: tuple[tuple[int, int], ...]
    expected_reason_codes: Mapping[int, frozenset[ReasonCode]]
    expected_prerequisite_ids: Mapping[int, tuple[int, ...]]
    expected_related_targets: Mapping[int, tuple[int, ...]]
    expected_outcome: PlanOutcome


def _planning_input(
    version_id: int, targets: tuple[int, ...], mastered: tuple[int, ...] = ()
) -> PlanningInput:
    return PlanningInput(
        graph_version_id=version_id,
        target_ids=targets,
        mastered_concept_ids=mastered,
        algorithm_version="fixture-v1",
    )


def _chain_version() -> VersionData:
    return _version(
        1,
        [_node(1, "N001", "A"), _node(2, "N002", "B"), _node(3, "N003", "D")],
        [_edge(11, 1, 2), _edge(12, 2, 3)],
    )


def _fork_version() -> VersionData:
    return _version(
        2,
        [_node(1, "N001", "A"), _node(2, "N002", "B"), _node(3, "N003", "C")],
        [_edge(21, 1, 2), _edge(22, 1, 3)],
    )


def _shared_deep_version() -> VersionData:
    return _version(
        3,
        [
            _node(1, "N001", "A"),
            _node(2, "N002", "B"),
            _node(3, "N003", "C"),
            _node(4, "N004", "D"),
            _node(5, "N005", "E"),
        ],
        [_edge(31, 1, 2), _edge(32, 2, 3), _edge(33, 3, 5), _edge(34, 1, 4), _edge(35, 4, 5)],
    )


def _isolated_version() -> VersionData:
    return _version(4, [_node(9, "N009", "I")], [])


def _target_as_prerequisite_version() -> VersionData:
    return _version(
        5,
        [_node(1, "N001", "A"), _node(2, "N002", "B"), _node(3, "N003", "C")],
        [_edge(41, 1, 2), _edge(42, 1, 3)],
    )


CHAIN = Scenario(
    name="chain",
    version=_chain_version(),
    planning_input=_planning_input(1, (3,)),
    expected_review=(1, 2, 3),
    expected_stages=((1,), (2,), (3,)),
    expected_target_status={3: TargetStatus.NEEDS_REVIEW},
    expected_boundaries=(),
    expected_boundary_edges=(),
    expected_dependency_edges=((1, 2), (2, 3)),
    expected_reason_codes={
        1: frozenset({ReasonCode.PREREQUISITE}),
        2: frozenset({ReasonCode.PREREQUISITE}),
        3: frozenset({ReasonCode.TARGET}),
    },
    expected_prerequisite_ids={1: (), 2: (1,), 3: (2,)},
    expected_related_targets={1: (3,), 2: (3,), 3: (3,)},
    expected_outcome=PlanOutcome.REVIEW_REQUIRED,
)

MASTERED_BOUNDARY = Scenario(
    name="mastered_boundary",
    version=_chain_version(),
    planning_input=_planning_input(1, (3,), mastered=(2,)),
    expected_review=(3,),
    expected_stages=((3,),),
    expected_target_status={3: TargetStatus.NEEDS_REVIEW},
    expected_boundaries=(2,),
    expected_boundary_edges=((2, 3),),
    expected_dependency_edges=(),
    expected_reason_codes={3: frozenset({ReasonCode.TARGET})},
    expected_prerequisite_ids={3: (2,)},
    expected_related_targets={3: (3,)},
    expected_outcome=PlanOutcome.REVIEW_REQUIRED,
)

FORK = Scenario(
    name="fork",
    version=_fork_version(),
    planning_input=_planning_input(2, (2, 3)),
    expected_review=(1, 2, 3),
    expected_stages=((1,), (2, 3)),
    expected_target_status={2: TargetStatus.NEEDS_REVIEW, 3: TargetStatus.NEEDS_REVIEW},
    expected_boundaries=(),
    expected_boundary_edges=(),
    expected_dependency_edges=((1, 2), (1, 3)),
    expected_reason_codes={
        1: frozenset({ReasonCode.PREREQUISITE}),
        2: frozenset({ReasonCode.TARGET}),
        3: frozenset({ReasonCode.TARGET}),
    },
    expected_prerequisite_ids={1: (), 2: (1,), 3: (1,)},
    expected_related_targets={1: (2, 3), 2: (2,), 3: (3,)},
    expected_outcome=PlanOutcome.REVIEW_REQUIRED,
)

SHARED_DEEP = Scenario(
    name="shared_deep",
    version=_shared_deep_version(),
    planning_input=_planning_input(3, (5,)),
    expected_review=(1, 2, 3, 4, 5),
    expected_stages=((1,), (2, 4), (3,), (5,)),
    expected_target_status={5: TargetStatus.NEEDS_REVIEW},
    expected_boundaries=(),
    expected_boundary_edges=(),
    expected_dependency_edges=((1, 2), (1, 4), (2, 3), (3, 5), (4, 5)),
    expected_reason_codes={
        1: frozenset({ReasonCode.PREREQUISITE}),
        2: frozenset({ReasonCode.PREREQUISITE}),
        3: frozenset({ReasonCode.PREREQUISITE}),
        4: frozenset({ReasonCode.PREREQUISITE}),
        5: frozenset({ReasonCode.TARGET}),
    },
    expected_prerequisite_ids={1: (), 2: (1,), 3: (2,), 4: (1,), 5: (3, 4)},
    expected_related_targets={1: (5,), 2: (5,), 3: (5,), 4: (5,), 5: (5,)},
    expected_outcome=PlanOutcome.REVIEW_REQUIRED,
)

ALL_MASTERED = Scenario(
    name="all_mastered",
    version=_chain_version(),
    planning_input=_planning_input(1, (3,), mastered=(1, 2, 3)),
    expected_review=(),
    expected_stages=(),
    expected_target_status={3: TargetStatus.MASTERED},
    expected_boundaries=(),
    expected_boundary_edges=(),
    expected_dependency_edges=(),
    expected_reason_codes={},
    expected_prerequisite_ids={},
    expected_related_targets={},
    expected_outcome=PlanOutcome.NO_REVIEW_NEEDED,
)

ISOLATED = Scenario(
    name="isolated",
    version=_isolated_version(),
    planning_input=_planning_input(4, (9,)),
    expected_review=(9,),
    expected_stages=((9,),),
    expected_target_status={9: TargetStatus.NEEDS_REVIEW},
    expected_boundaries=(),
    expected_boundary_edges=(),
    expected_dependency_edges=(),
    expected_reason_codes={9: frozenset({ReasonCode.TARGET})},
    expected_prerequisite_ids={9: ()},
    expected_related_targets={9: (9,)},
    expected_outcome=PlanOutcome.REVIEW_REQUIRED,
)

TARGET_AS_PREREQUISITE = Scenario(
    name="target_as_prerequisite",
    version=_target_as_prerequisite_version(),
    planning_input=_planning_input(5, (1, 2)),
    expected_review=(1, 2),
    expected_stages=((1,), (2,)),
    expected_target_status={1: TargetStatus.NEEDS_REVIEW, 2: TargetStatus.NEEDS_REVIEW},
    expected_boundaries=(),
    expected_boundary_edges=(),
    expected_dependency_edges=((1, 2),),
    expected_reason_codes={
        1: frozenset({ReasonCode.TARGET, ReasonCode.PREREQUISITE}),
        2: frozenset({ReasonCode.TARGET}),
    },
    expected_prerequisite_ids={1: (), 2: (1,)},
    expected_related_targets={1: (1, 2), 2: (2,)},
    expected_outcome=PlanOutcome.REVIEW_REQUIRED,
)

SCENARIOS: tuple[Scenario, ...] = (
    CHAIN,
    MASTERED_BOUNDARY,
    FORK,
    SHARED_DEEP,
    ALL_MASTERED,
    ISOLATED,
    TARGET_AS_PREREQUISITE,
)
