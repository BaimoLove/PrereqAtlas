"""版本/规划共享契约与冻结夹具的一致性。

这里不实现追溯或分阶段算法；只验证契约模型可表达夹具，
且夹具的精确期望在自身范围内自洽（阶段、边界、目标关联、理由）。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.common.contracts import (
    MasteredBoundary,
    PlanningEdge,
    PlanningInput,
    PlanningNode,
    PlanningResult,
    PlanningStage,
    PlanningTarget,
    PlanOutcome,
    ReasonCode,
    ReviewStatus,
    TargetStatus,
    VersionData,
)
from fixtures.graphs import SCENARIOS, Scenario


def _stage_map(scenario: Scenario) -> dict[int, int]:
    return {
        concept_id: stage_index
        for stage_index, stage in enumerate(scenario.expected_stages, start=1)
        for concept_id in stage
    }


def _adopted_edges(scenario: Scenario) -> list[tuple[int, int]]:
    return [
        (edge.source_id, edge.target_id)
        for edge in scenario.version.edges
        if edge.review_status is ReviewStatus.APPROVED
    ]


def _is_dag(nodes: list[int], edges: list[tuple[int, int]]) -> bool:
    indegree = dict.fromkeys(nodes, 0)
    adjacency: dict[int, list[int]] = {node: [] for node in nodes}
    for source, target in edges:
        adjacency[source].append(target)
        indegree[target] += 1
    ready = [node for node, degree in indegree.items() if degree == 0]
    visited = 0
    while ready:
        node = ready.pop()
        visited += 1
        for nxt in adjacency[node]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                ready.append(nxt)
    return visited == len(nodes)


def test_required_scenarios_are_present() -> None:
    names = {scenario.name for scenario in SCENARIOS}
    assert names == {
        "chain",
        "mastered_boundary",
        "fork",
        "shared_deep",
        "all_mastered",
        "isolated",
        "target_as_prerequisite",
    }


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_fixture_graph_is_well_formed(scenario: Scenario) -> None:
    nodes = [node.concept_id for node in scenario.version.nodes]
    assert len(nodes) == len(set(nodes))
    edge_ids = [edge.relation_id for edge in scenario.version.edges]
    assert len(edge_ids) == len(set(edge_ids))
    for edge in scenario.version.edges:
        assert edge.source_id in nodes
        assert edge.target_id in nodes
        assert edge.source_id != edge.target_id
    assert _is_dag(nodes, _adopted_edges(scenario))


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_fixture_stages_partition_review_set(scenario: Scenario) -> None:
    flattened = [concept_id for stage in scenario.expected_stages for concept_id in stage]
    assert len(flattened) == len(set(flattened))
    assert set(flattened) == set(scenario.expected_review)
    stage_map = _stage_map(scenario)
    assert set(stage_map) == set(scenario.expected_review)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_fixture_stage_order_respects_review_dependency_edges(scenario: Scenario) -> None:
    stage_map = _stage_map(scenario)
    for source, target in scenario.expected_dependency_edges:
        assert source in stage_map
        assert target in stage_map
        assert stage_map[source] < stage_map[target], (source, target)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_fixture_target_status_and_boundaries(scenario: Scenario) -> None:
    target_ids = set(scenario.planning_input.target_ids)
    assert set(scenario.expected_target_status) == target_ids
    for concept_id, status in scenario.expected_target_status.items():
        if status is TargetStatus.NEEDS_REVIEW:
            assert concept_id in scenario.expected_review
        else:
            assert concept_id not in scenario.expected_review

    mastered = set(scenario.planning_input.mastered_concept_ids)
    assert set(scenario.expected_boundaries) <= mastered
    assert set(scenario.expected_boundaries).isdisjoint(scenario.expected_review)
    for source, target in scenario.expected_boundary_edges:
        assert source in scenario.expected_boundaries
        assert target in scenario.expected_review


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_fixture_explanations_are_complete(scenario: Scenario) -> None:
    review = set(scenario.expected_review)
    targets = set(scenario.planning_input.target_ids)
    boundaries = set(scenario.expected_boundaries)
    assert set(scenario.expected_reason_codes) == review
    assert set(scenario.expected_prerequisite_ids) == review
    assert set(scenario.expected_related_targets) == review
    for concept_id in review:
        reasons = scenario.expected_reason_codes[concept_id]
        assert reasons
        assert set(scenario.expected_related_targets[concept_id]) <= targets
        assert scenario.expected_related_targets[concept_id]
        assert set(scenario.expected_prerequisite_ids[concept_id]) <= (review | boundaries)
        if concept_id in targets:
            assert ReasonCode.TARGET in reasons
        else:
            assert ReasonCode.TARGET not in reasons


def test_version_data_is_frozen_and_hash_is_hex64() -> None:
    scenario = SCENARIOS[0]
    with pytest.raises(ValidationError):
        VersionData(
            version_id=scenario.version.version_id,
            content_hash="not-a-hash",
            chapters=scenario.version.chapters,
            nodes=scenario.version.nodes,
            edges=scenario.version.edges,
        )
    with pytest.raises(ValidationError):
        scenario.version.version_id = 99  # type: ignore[misc]


def test_planning_input_boundaries_and_duplicates() -> None:
    PlanningInput(
        graph_version_id=1,
        target_ids=(1,),
        mastered_concept_ids=(),
        algorithm_version="v1",
    )
    for targets in ((), tuple(range(1, 22))):
        with pytest.raises(ValidationError):
            PlanningInput(
                graph_version_id=1,
                target_ids=targets,
                mastered_concept_ids=(),
                algorithm_version="v1",
            )
    duplicated = PlanningInput(
        graph_version_id=1,
        target_ids=(3, 3, 1),
        mastered_concept_ids=(),
        algorithm_version="v1",
    )
    assert duplicated.target_ids == (3, 3, 1)


def _build_result(scenario: Scenario) -> PlanningResult:
    stage_by_concept = _stage_map(scenario)
    relation_by_edge = {
        (edge.source_id, edge.target_id): edge for edge in scenario.version.edges
    }
    dependency_edges = tuple(
        PlanningEdge(
            source_id=source,
            target_id=target,
            relation_id=relation_by_edge[(source, target)].relation_id,
            review_basis="fixture",
        )
        for source, target in scenario.expected_dependency_edges
    )
    boundary_edges = tuple(
        PlanningEdge(
            source_id=source,
            target_id=target,
            relation_id=relation_by_edge[(source, target)].relation_id,
            review_basis="fixture",
        )
        for source, target in scenario.expected_boundary_edges
    )
    nodes = tuple(
        PlanningNode(
            concept_id=concept_id,
            stage_index=stage_by_concept[concept_id],
            reason_codes=tuple(
                sorted(scenario.expected_reason_codes[concept_id], key=lambda code: code.value)
            ),
            related_target_ids=scenario.expected_related_targets[concept_id],
            prerequisite_ids=scenario.expected_prerequisite_ids[concept_id],
        )
        for concept_id in scenario.expected_review
    )
    stages = tuple(
        PlanningStage(stage_index=index, concept_ids=stage)
        for index, stage in enumerate(scenario.expected_stages, start=1)
    )
    boundaries = tuple(
        MasteredBoundary(
            concept_id=boundary,
            related_target_ids=scenario.planning_input.target_ids,
            boundary_edges=boundary_edges,
        )
        for boundary in scenario.expected_boundaries
    )
    return PlanningResult(
        graph_version_id=scenario.version.version_id,
        graph_content_hash=scenario.version.content_hash or "",
        algorithm_version="fixture-v1",
        targets=tuple(
            PlanningTarget(concept_id=target, status=status)
            for target, status in sorted(scenario.expected_target_status.items())
        ),
        stages=stages,
        nodes=nodes,
        dependency_edges=dependency_edges,
        mastered_boundaries=boundaries,
        outcome=scenario.expected_outcome,
    )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_planning_result_can_represent_each_fixture(scenario: Scenario) -> None:
    result = _build_result(scenario)
    payload = result.model_dump(mode="json")
    assert payload["graph_version_id"] == scenario.version.version_id
    assert set(payload.keys()) == {
        "graph_version_id",
        "graph_content_hash",
        "algorithm_version",
        "targets",
        "stages",
        "nodes",
        "dependency_edges",
        "mastered_boundaries",
        "outcome",
    }
    assert [stage["stage_index"] for stage in payload["stages"]] == list(
        range(1, len(scenario.expected_stages) + 1)
    )


def test_planning_result_outcome_invariants() -> None:
    target = PlanningTarget(concept_id=1, status=TargetStatus.MASTERED)
    PlanningResult(
        graph_version_id=1,
        graph_content_hash="a" * 64,
        algorithm_version="v1",
        targets=(target,),
        stages=(),
        nodes=(),
        dependency_edges=(),
        mastered_boundaries=(),
        outcome=PlanOutcome.NO_REVIEW_NEEDED,
    )
    with pytest.raises(ValidationError):
        PlanningResult(
            graph_version_id=1,
            graph_content_hash="a" * 64,
            algorithm_version="v1",
            targets=(target,),
            stages=(PlanningStage(stage_index=1, concept_ids=(1,)),),
            nodes=(),
            dependency_edges=(),
            mastered_boundaries=(),
            outcome=PlanOutcome.NO_REVIEW_NEEDED,
        )
    with pytest.raises(ValidationError):
        PlanningResult(
            graph_version_id=1,
            graph_content_hash="a" * 64,
            algorithm_version="v1",
            targets=(target,),
            stages=(),
            nodes=(),
            dependency_edges=(),
            mastered_boundaries=(),
            outcome=PlanOutcome.REVIEW_REQUIRED,
        )


def test_planning_stage_and_node_bounds() -> None:
    with pytest.raises(ValidationError):
        PlanningStage(stage_index=0, concept_ids=(1,))
    with pytest.raises(ValidationError):
        PlanningStage(stage_index=1, concept_ids=())
    with pytest.raises(ValidationError):
        PlanningNode(
            concept_id=1,
            stage_index=0,
            reason_codes=(ReasonCode.TARGET,),
            related_target_ids=(1,),
            prerequisite_ids=(),
        )
    with pytest.raises(ValidationError):
        PlanningNode(
            concept_id=1,
            stage_index=1,
            reason_codes=(),
            related_target_ids=(1,),
            prerequisite_ids=(),
        )


def test_duplicate_stage_index_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PlanningResult(
            graph_version_id=1,
            graph_content_hash="a" * 64,
            algorithm_version="v1",
            targets=(PlanningTarget(concept_id=1, status=TargetStatus.NEEDS_REVIEW),),
            stages=(
                PlanningStage(stage_index=1, concept_ids=(1,)),
                PlanningStage(stage_index=1, concept_ids=(2,)),
            ),
            nodes=(
                PlanningNode(
                    concept_id=1,
                    stage_index=1,
                    reason_codes=(ReasonCode.TARGET,),
                    related_target_ids=(1,),
                    prerequisite_ids=(),
                ),
            ),
            dependency_edges=(),
            mastered_boundaries=(),
            outcome=PlanOutcome.REVIEW_REQUIRED,
        )
