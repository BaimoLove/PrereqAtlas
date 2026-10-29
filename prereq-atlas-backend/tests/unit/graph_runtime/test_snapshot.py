"""T13 只读图快照：纯内存构图，不访问数据库/HTTP/文件系统。

验收映射：
- T13-01：全部概念进入快照，无关系的概念不被丢弃；
- T13-02：只有 APPROVED + REQUIRED 关系成为依赖边；
- T13-05：未知端点、自环、重复边、环明确失败并定位到具体元素。
"""

from __future__ import annotations

import pytest

from app.common.contracts import (
    Necessity,
    ReviewStatus,
    VersionData,
    VersionEdge,
    VersionNode,
)
from app.modules.graph_runtime.public import GraphDataError, build_snapshot

REQUIRED = Necessity.REQUIRED
APPROVED = ReviewStatus.APPROVED
HASH_A = "a" * 64


def node(concept_id: int) -> VersionNode:
    return VersionNode(
        concept_id=concept_id,
        code=f"N{concept_id:04d}",
        label_zh=f"概念{concept_id}",
    )


def edge(
    relation_id: int,
    source_id: int,
    target_id: int,
    *,
    necessity: Necessity = REQUIRED,
    review_status: ReviewStatus = APPROVED,
) -> VersionEdge:
    return VersionEdge(
        relation_id=relation_id,
        source_id=source_id,
        target_id=target_id,
        necessity=necessity,
        review_status=review_status,
    )


def version(
    nodes: list[VersionNode],
    edges: list[VersionEdge],
    *,
    version_id: int = 7,
    content_hash: str | None = HASH_A,
) -> VersionData:
    return VersionData(
        version_id=version_id,
        content_hash=content_hash,
        nodes=tuple(nodes),
        edges=tuple(edges),
    )


def test_all_concepts_enter_the_snapshot_and_unrelated_ones_stay_isolated() -> None:
    chain_sizes = (12, 12, 12, 12, 12, 12, 9, 9)
    nodes = [node(concept_id) for concept_id in range(1, 121)]
    edges: list[VersionEdge] = []
    relation_id = 1000
    first = 1
    for size in chain_sizes:
        for offset in range(size - 1):
            relation_id += 1
            edges.append(edge(relation_id, first + offset, first + offset + 1))
        first += size

    snapshot = build_snapshot(version(nodes, edges))

    assert len(snapshot.nodes) == 120
    assert len(snapshot.edges) == 82
    assert set(snapshot.isolated_concept_ids) == set(range(91, 121))
    assert snapshot.prerequisites_of(2) == (1,)
    assert snapshot.successors_of(1) == (2,)


def test_only_approved_required_relations_become_dependency_edges() -> None:
    nodes = [node(1), node(2), node(3)]
    edges = [
        edge(101, 1, 2),
        edge(102, 1, 3, review_status=ReviewStatus.PENDING),
        edge(103, 2, 3, necessity=Necessity.RECOMMENDED),
        edge(104, 3, 1, review_status=ReviewStatus.REJECTED),
        edge(105, 2, 1, necessity=Necessity.OPTIONAL),
    ]

    snapshot = build_snapshot(version(nodes, edges))

    assert [item.relation_id for item in snapshot.edges] == [101]
    assert snapshot.prerequisites_of(2) == (1,)
    assert snapshot.prerequisites_of(3) == ()
    assert set(snapshot.isolated_concept_ids) == {3}


def test_unknown_endpoint_is_reported_with_its_concept_and_relation() -> None:
    data = version([node(1), node(2)], [edge(201, 1, 9)])

    with pytest.raises(GraphDataError) as raised:
        build_snapshot(data)

    issues = raised.value.issues
    assert [issue.code for issue in issues] == ["UNKNOWN_ENDPOINT"]
    assert issues[0].concept_ids == (9,)
    assert issues[0].relation_ids == (201,)


def test_self_loop_is_reported_and_never_becomes_an_edge() -> None:
    data = version([node(1)], [edge(202, 1, 1)])

    with pytest.raises(GraphDataError) as raised:
        build_snapshot(data)

    issues = raised.value.issues
    assert [issue.code for issue in issues] == ["SELF_LOOP"]
    assert issues[0].concept_ids == (1,)
    assert issues[0].relation_ids == (202,)


def test_duplicate_adopted_edge_is_reported_with_both_relations() -> None:
    data = version([node(1), node(2)], [edge(203, 1, 2), edge(204, 1, 2)])

    with pytest.raises(GraphDataError) as raised:
        build_snapshot(data)

    issues = raised.value.issues
    assert [issue.code for issue in issues] == ["DUPLICATE_EDGE"]
    assert issues[0].relation_ids == (203, 204)


def test_cycle_is_reported_as_a_closed_node_path() -> None:
    data = version(
        [node(1), node(2), node(3)],
        [edge(205, 1, 2), edge(206, 2, 3), edge(207, 3, 1)],
    )

    with pytest.raises(GraphDataError) as raised:
        build_snapshot(data)

    assert [issue.code for issue in raised.value.issues] == ["CYCLE"]
    assert raised.value.cycle_paths == ((1, 2, 3, 1),)


def test_every_problem_is_collected_in_one_build() -> None:
    data = version(
        [node(1), node(2)],
        [edge(208, 1, 9), edge(209, 2, 2), edge(210, 1, 2), edge(211, 1, 2)],
    )

    with pytest.raises(GraphDataError) as raised:
        build_snapshot(data)

    located = {issue.code: issue.relation_ids for issue in raised.value.issues}
    assert located == {
        "UNKNOWN_ENDPOINT": (208,),
        "SELF_LOOP": (209,),
        "DUPLICATE_EDGE": (210, 211),
    }
