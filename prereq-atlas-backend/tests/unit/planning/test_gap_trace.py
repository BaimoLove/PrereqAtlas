"""T17 纯缺口追溯：不访问数据库、HTTP 或文件系统。

验收映射：
- T17-01：目标 D 的整条先修链全部进入待复习集合；
- T17-02：已掌握节点终止追溯，只留下边界；
- T17-03：另一分支的目标仍把共享先修纳入；
- T17-04：深层共享先修只出现一次且关联全部未掌握目标；
- T17-05：目标兼作先修时保留双重原因；
- T17-06：已掌握目标不继续追溯，全部掌握得空集合；
- T17-07：孤立目标保留，空/未知目标拒绝、重复目标去重；
- T17-08：输入与快照前后不变，外部 I/O 次数 0。

期望值来自 T02 冻结夹具的字面量，不由被测实现推导。
"""

from __future__ import annotations

import builtins
import socket
from collections.abc import Callable

import pytest
from pydantic import ValidationError

from app.common.contracts import PlanningEdge, PlanningInput, PlanningTarget, TargetStatus
from app.modules.graph_runtime.public import GraphSnapshot, build_snapshot
from app.modules.planning.public import (
    UNKNOWN_TARGET,
    VERSION_MISMATCH,
    GapTrace,
    PlanningDataError,
    trace_gaps,
)
from fixtures.graphs import (
    ALL_MASTERED,
    CHAIN,
    FORK,
    ISOLATED,
    MASTERED_BOUNDARY,
    SHARED_DEEP,
    TARGET_AS_PREREQUISITE,
    Scenario,
)


def review_ids(trace: GapTrace) -> list[int]:
    return [node.concept_id for node in trace.nodes]


def reason_map(trace: GapTrace) -> dict[int, frozenset[str]]:
    return {node.concept_id: frozenset(node.reason_codes) for node in trace.nodes}


def related_map(trace: GapTrace) -> dict[int, tuple[int, ...]]:
    return {node.concept_id: node.related_target_ids for node in trace.nodes}


def prerequisite_map(trace: GapTrace) -> dict[int, tuple[int, ...]]:
    return {node.concept_id: node.prerequisite_ids for node in trace.nodes}


def edge_pairs(edges: tuple[PlanningEdge, ...]) -> list[tuple[int, int]]:
    return [(edge.source_id, edge.target_id) for edge in edges]


def boundary_ids(trace: GapTrace) -> list[int]:
    return [boundary.concept_id for boundary in trace.mastered_boundaries]


def boundary_edge_pairs(trace: GapTrace) -> list[tuple[int, int]]:
    return [
        (edge.source_id, edge.target_id)
        for boundary in trace.mastered_boundaries
        for edge in boundary.boundary_edges
    ]


def target_status_map(trace: GapTrace) -> dict[int, TargetStatus]:
    return {target.concept_id: target.status for target in trace.targets}


def trace_of(scenario: Scenario) -> tuple[GraphSnapshot, GapTrace]:
    snapshot = build_snapshot(scenario.version)
    return snapshot, trace_gaps(snapshot, scenario.planning_input)


def test_target_traces_its_whole_prerequisite_chain() -> None:
    trace = trace_of(CHAIN)[1]

    assert review_ids(trace) == list(CHAIN.expected_review)
    assert reason_map(trace) == CHAIN.expected_reason_codes
    assert target_status_map(trace) == CHAIN.expected_target_status
    assert edge_pairs(trace.dependency_edges) == list(CHAIN.expected_dependency_edges)
    assert prerequisite_map(trace) == CHAIN.expected_prerequisite_ids
    assert related_map(trace) == CHAIN.expected_related_targets
    assert boundary_ids(trace) == list(CHAIN.expected_boundaries)
    assert trace.graph_version_id == CHAIN.version.version_id
    assert trace.content_hash == CHAIN.version.content_hash


def test_mastered_prerequisite_stops_the_branch_and_becomes_a_boundary() -> None:
    trace = trace_of(MASTERED_BOUNDARY)[1]

    assert review_ids(trace) == list(MASTERED_BOUNDARY.expected_review)
    assert target_status_map(trace) == MASTERED_BOUNDARY.expected_target_status
    assert reason_map(trace) == MASTERED_BOUNDARY.expected_reason_codes
    assert prerequisite_map(trace) == MASTERED_BOUNDARY.expected_prerequisite_ids
    assert related_map(trace) == MASTERED_BOUNDARY.expected_related_targets
    assert edge_pairs(trace.dependency_edges) == list(
        MASTERED_BOUNDARY.expected_dependency_edges
    )
    assert boundary_ids(trace) == list(MASTERED_BOUNDARY.expected_boundaries)
    assert boundary_edge_pairs(trace) == list(MASTERED_BOUNDARY.expected_boundary_edges)


def test_shared_prerequisite_keeps_every_target_it_belongs_to() -> None:
    trace = trace_of(FORK)[1]

    assert review_ids(trace) == list(FORK.expected_review)
    assert target_status_map(trace) == FORK.expected_target_status
    assert reason_map(trace) == FORK.expected_reason_codes
    assert prerequisite_map(trace) == FORK.expected_prerequisite_ids
    assert related_map(trace) == FORK.expected_related_targets
    assert edge_pairs(trace.dependency_edges) == list(FORK.expected_dependency_edges)
    assert boundary_ids(trace) == list(FORK.expected_boundaries)


def test_deep_shared_prerequisite_is_collected_once() -> None:
    trace = trace_of(SHARED_DEEP)[1]

    assert review_ids(trace) == list(SHARED_DEEP.expected_review)
    assert review_ids(trace).count(1) == 1
    assert reason_map(trace) == SHARED_DEEP.expected_reason_codes
    assert prerequisite_map(trace) == SHARED_DEEP.expected_prerequisite_ids
    assert related_map(trace) == SHARED_DEEP.expected_related_targets
    assert edge_pairs(trace.dependency_edges) == list(SHARED_DEEP.expected_dependency_edges)
    assert boundary_ids(trace) == list(SHARED_DEEP.expected_boundaries)


def test_target_that_is_also_a_prerequisite_keeps_both_reasons() -> None:
    trace = trace_of(TARGET_AS_PREREQUISITE)[1]

    assert review_ids(trace) == list(TARGET_AS_PREREQUISITE.expected_review)
    assert reason_map(trace) == TARGET_AS_PREREQUISITE.expected_reason_codes
    assert related_map(trace) == TARGET_AS_PREREQUISITE.expected_related_targets
    assert prerequisite_map(trace) == TARGET_AS_PREREQUISITE.expected_prerequisite_ids
    assert edge_pairs(trace.dependency_edges) == list(
        TARGET_AS_PREREQUISITE.expected_dependency_edges
    )


def test_mastered_targets_need_no_review() -> None:
    trace = trace_of(ALL_MASTERED)[1]

    assert review_ids(trace) == list(ALL_MASTERED.expected_review)
    assert target_status_map(trace) == ALL_MASTERED.expected_target_status
    assert reason_map(trace) == ALL_MASTERED.expected_reason_codes
    assert trace.mastered_boundaries == ()
    assert trace.dependency_edges == ()


def test_mastered_target_does_not_trace_its_own_prerequisites() -> None:
    snapshot = build_snapshot(CHAIN.version)
    planning = PlanningInput(
        graph_version_id=1,
        target_ids=(2,),
        mastered_concept_ids=(2,),
        algorithm_version="fixture-v1",
    )

    trace = trace_gaps(snapshot, planning)

    assert trace.targets == (PlanningTarget(concept_id=2, status=TargetStatus.MASTERED),)
    assert trace.nodes == ()
    assert trace.mastered_boundaries == ()


def test_isolated_target_without_prerequisites_stays_in_the_review_set() -> None:
    trace = trace_of(ISOLATED)[1]

    assert review_ids(trace) == list(ISOLATED.expected_review)
    assert reason_map(trace) == ISOLATED.expected_reason_codes
    assert prerequisite_map(trace) == ISOLATED.expected_prerequisite_ids
    assert trace.dependency_edges == ()
    assert trace.mastered_boundaries == ()


def test_unknown_target_is_rejected_with_its_id() -> None:
    snapshot = build_snapshot(CHAIN.version)
    planning = PlanningInput(
        graph_version_id=1, target_ids=(99,), algorithm_version="fixture-v1"
    )

    with pytest.raises(PlanningDataError) as raised:
        trace_gaps(snapshot, planning)

    assert [(issue.code, issue.concept_ids) for issue in raised.value.issues] == [
        (UNKNOWN_TARGET, (99,))
    ]


def test_mismatched_graph_version_is_rejected() -> None:
    snapshot = build_snapshot(CHAIN.version)
    planning = PlanningInput(
        graph_version_id=9, target_ids=(3,), algorithm_version="fixture-v1"
    )

    with pytest.raises(PlanningDataError) as raised:
        trace_gaps(snapshot, planning)

    assert [issue.code for issue in raised.value.issues] == [VERSION_MISMATCH]


def test_empty_targets_are_rejected_before_tracing() -> None:
    with pytest.raises(ValidationError):
        PlanningInput(graph_version_id=1, target_ids=(), algorithm_version="fixture-v1")


def test_duplicate_targets_are_merged_into_one() -> None:
    snapshot = build_snapshot(CHAIN.version)
    planning = PlanningInput(
        graph_version_id=1, target_ids=(3, 3, 1), algorithm_version="fixture-v1"
    )

    trace = trace_gaps(snapshot, planning)

    assert [target.concept_id for target in trace.targets] == [3, 1]
    assert review_ids(trace) == [1, 2, 3]
    assert related_map(trace) == {1: (1, 3), 2: (3,), 3: (3,)}


def test_tracing_keeps_its_inputs_untouched_and_reads_nothing_external(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = build_snapshot(CHAIN.version)
    same_snapshot = build_snapshot(CHAIN.version)
    planning = CHAIN.planning_input
    planning_before = planning.model_copy()

    touched: list[str] = []

    def forbid(name: str) -> Callable[..., object]:
        def guard(*args: object, **kwargs: object) -> object:
            touched.append(name)
            raise AssertionError(f"trace_gaps 不得访问 {name}")

        return guard

    monkeypatch.setattr(builtins, "open", forbid("open"))
    monkeypatch.setattr(socket.socket, "connect", forbid("socket.connect"))
    monkeypatch.setattr(socket, "create_connection", forbid("socket.create_connection"))

    trace = trace_gaps(snapshot, planning)
    repeated = trace_gaps(snapshot, planning)

    assert touched == []
    assert planning == planning_before
    assert snapshot == same_snapshot
    assert trace == repeated
