"""T13 快照缓存：按 version/hash 复用，键不同互不污染，读取不可变，构图零外部访问。

验收映射：
- T13-03：同版本同内容哈希命中缓存；版本或哈希不同则互不串用；
- T13-04：结构、节点属性与嵌套资料都不可修改，失败构建不会污染下一次读取；
- T13-06：构建与缓存过程不访问数据库、HTTP 或文件系统。
"""

from __future__ import annotations

import builtins
import socket
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from app.common.contracts import (
    Necessity,
    ReviewStatus,
    VersionData,
    VersionEdge,
    VersionNode,
    VersionResource,
)
from app.modules.graph_runtime.public import (
    GraphDataError,
    SnapshotCache,
    build_snapshot,
)

REQUIRED = Necessity.REQUIRED
APPROVED = ReviewStatus.APPROVED
HASH_A = "a" * 64
HASH_B = "b" * 64


def node(concept_id: int, *, resources: tuple[VersionResource, ...] = ()) -> VersionNode:
    return VersionNode(
        concept_id=concept_id,
        code=f"N{concept_id:04d}",
        label_zh=f"概念{concept_id}",
        resources=resources,
    )


def edge(relation_id: int, source_id: int, target_id: int) -> VersionEdge:
    return VersionEdge(
        relation_id=relation_id,
        source_id=source_id,
        target_id=target_id,
        necessity=REQUIRED,
        review_status=APPROVED,
    )


def version(
    nodes: list[VersionNode],
    edges: list[VersionEdge] | None = None,
    *,
    version_id: int = 7,
    content_hash: str | None = HASH_A,
) -> VersionData:
    return VersionData(
        version_id=version_id,
        content_hash=content_hash,
        nodes=tuple(nodes),
        edges=tuple(edges or []),
    )


def test_same_version_and_hash_reuses_the_built_snapshot() -> None:
    cache = SnapshotCache()
    first = cache.get_or_build(version([node(1)]))
    # 同键但内容不同：必须复用已构建的快照，而不是重新混入新内容
    second = cache.get_or_build(version([node(1), node(2)]))

    assert second is first
    assert [item.concept_id for item in second.nodes] == [1]


def test_different_version_or_hash_never_mixes_snapshots() -> None:
    cache = SnapshotCache()
    base = cache.get_or_build(version([node(1)], version_id=7, content_hash=HASH_A))
    other_version = cache.get_or_build(
        version([node(2)], version_id=8, content_hash=HASH_A)
    )
    other_hash = cache.get_or_build(
        version([node(3)], version_id=7, content_hash=HASH_B)
    )

    assert [item.concept_id for item in base.nodes] == [1]
    assert [item.concept_id for item in other_version.nodes] == [2]
    assert [item.concept_id for item in other_hash.nodes] == [3]
    assert base.version_id == 7
    assert other_version.version_id == 8


def test_snapshot_cannot_be_mutated_nor_pollute_the_cache() -> None:
    cache = SnapshotCache()
    data = version([node(1), node(2)], [edge(11, 1, 2)])
    snapshot = cache.get_or_build(data)

    with pytest.raises(AttributeError):
        snapshot.nodes.append(node(3))  # type: ignore[attr-defined]
    with pytest.raises(TypeError):
        snapshot.prerequisites[1] = ()  # type: ignore[index]
    with pytest.raises(ValidationError):
        snapshot.nodes[0].label_zh = "篡改"
    with pytest.raises(TypeError):
        snapshot.edges[0] = snapshot.edges[0]  # type: ignore[index]

    fresh = cache.get_or_build(data)
    assert fresh.nodes[0].label_zh == "概念1"
    assert fresh.prerequisites_of(2) == (1,)
    assert [item.relation_id for item in fresh.edges] == [11]


def test_nested_resources_are_immutable_too() -> None:
    resource = VersionResource(
        resource_id=5, title="资料", url="https://example.com/resource"
    )
    snapshot = build_snapshot(version([node(1, resources=(resource,))]))

    with pytest.raises(ValidationError):
        snapshot.nodes[0].resources[0].title = "篡改"


def test_failed_build_is_not_cached() -> None:
    cache = SnapshotCache()
    broken = version([node(1)], [edge(12, 1, 1)])

    with pytest.raises(GraphDataError):
        cache.get_or_build(broken)
    assert len(cache) == 0

    healthy = version([node(1)])
    snapshot = cache.get_or_build(healthy)
    assert [item.concept_id for item in snapshot.nodes] == [1]
    assert len(cache) == 1


def test_building_and_caching_never_reach_the_database_http_or_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hits = {"socket": 0, "http": 0, "file": 0}

    real_connect = socket.socket.connect

    def counting_connect(self: socket.socket, address: Any) -> Any:
        hits["socket"] += 1
        return real_connect(self, address)

    real_create_connection = socket.create_connection

    def counting_create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
        hits["http"] += 1
        return real_create_connection(address, *args, **kwargs)

    real_open: Callable[..., Any] = builtins.open

    def counting_open(*args: Any, **kwargs: Any) -> Any:
        hits["file"] += 1
        return real_open(*args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", counting_connect)
    monkeypatch.setattr(socket, "create_connection", counting_create_connection)
    monkeypatch.setattr(builtins, "open", counting_open)

    cache = SnapshotCache()
    nodes = [node(concept_id) for concept_id in range(1, 121)]
    edges = [edge(100 + offset, offset, offset + 1) for offset in range(1, 90)]
    for data in (
        version(nodes, edges),
        version(nodes, edges, content_hash=HASH_B),
    ):
        snapshot = cache.get_or_build(data)
        assert len(snapshot.nodes) == 120

    assert hits == {"socket": 0, "http": 0, "file": 0}
