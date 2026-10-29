"""把完整的版本数据构建成不可变图快照。

构图顺序固定：先放入全部概念，再放入可采用的关系，最后检查依赖图合法性。
合法性的唯一依据是「审核通过 + 必要」的关系：其他关系记录不进入图，也就不会
影响先修关系。任何不合法的版本数据都以 ``GraphDataError`` 失败，并定位到具体的
概念、关系或环节点链，绝不静默丢边。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from app.common.contracts import (
    Necessity,
    ReviewStatus,
    VersionData,
    VersionEdge,
    VersionNode,
)

ADOPTED_NECESSITY = Necessity.REQUIRED
ADOPTED_REVIEW_STATUS = ReviewStatus.APPROVED

UNKNOWN_ENDPOINT = "UNKNOWN_ENDPOINT"
SELF_LOOP = "SELF_LOOP"
DUPLICATE_EDGE = "DUPLICATE_EDGE"
CYCLE = "CYCLE"

_WHITE = 0
_GREY = 1
_BLACK = 2


@dataclass(frozen=True, slots=True)
class GraphIssue:
    """一个阻止构图的数据问题；定位到具体概念、关系或环节点链。"""

    code: str
    concept_ids: tuple[int, ...] = ()
    relation_ids: tuple[int, ...] = ()
    message: str = ""
    cycle_path: tuple[int, ...] | None = None


class GraphDataError(Exception):
    """版本数据无法构成合法依赖图；一次构图收集全部问题。"""

    def __init__(self, issues: Sequence[GraphIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__(
            "；".join(issue.message for issue in self.issues) or "版本数据无法构成依赖图"
        )

    @property
    def cycle_paths(self) -> tuple[tuple[int, ...], ...]:
        return tuple(
            issue.cycle_path for issue in self.issues if issue.cycle_path is not None
        )


@dataclass(frozen=True, slots=True)
class GraphSnapshot:
    """固定版本的只读图：全部概念 + 已采用的先修边，外加双向邻接索引。

    所有字段都是不可变结构；构建过程中的中间字典不会泄露，调用方也无法通过
    修改快照污染缓存。
    """

    version_id: int
    content_hash: str | None
    nodes: tuple[VersionNode, ...]
    edges: tuple[VersionEdge, ...]
    node_index: Mapping[int, VersionNode]
    prerequisites: Mapping[int, tuple[int, ...]]
    successors: Mapping[int, tuple[int, ...]]
    isolated_concept_ids: tuple[int, ...]

    def concept(self, concept_id: int) -> VersionNode:
        """返回概念内容；未知概念直接失败，避免把缺失当作空内容。"""
        return self.node_index[concept_id]

    def prerequisites_of(self, concept_id: int) -> tuple[int, ...]:
        return self.prerequisites.get(concept_id, ())

    def successors_of(self, concept_id: int) -> tuple[int, ...]:
        return self.successors.get(concept_id, ())


def adopt_edge(edge: VersionEdge) -> bool:
    """只有审核通过且必要的关系才进入先修依赖视图。"""
    return (
        edge.review_status is ADOPTED_REVIEW_STATUS
        and edge.necessity is ADOPTED_NECESSITY
    )


def build_snapshot(data: VersionData) -> GraphSnapshot:
    """构建快照；数据不合法时抛出 ``GraphDataError``（不返回半成品）。"""
    node_index = {node.concept_id: node for node in data.nodes}
    issues: list[GraphIssue] = []
    adopted: list[VersionEdge] = []
    duplicate_ids: dict[tuple[int, int], list[int]] = {}

    for item in data.edges:
        if not adopt_edge(item):
            continue
        missing = tuple(
            sorted(
                {
                    endpoint
                    for endpoint in (item.source_id, item.target_id)
                    if endpoint not in node_index
                }
            )
        )
        if missing:
            issues.append(
                GraphIssue(
                    code=UNKNOWN_ENDPOINT,
                    concept_ids=missing,
                    relation_ids=(item.relation_id,),
                    message=f"关系 {item.relation_id} 指向不存在的概念 {list(missing)}",
                )
            )
            continue
        if item.source_id == item.target_id:
            issues.append(
                GraphIssue(
                    code=SELF_LOOP,
                    concept_ids=(item.source_id,),
                    relation_ids=(item.relation_id,),
                    message=f"关系 {item.relation_id} 是概念 {item.source_id} 的自环",
                )
            )
            continue
        pair = (item.source_id, item.target_id)
        if pair in duplicate_ids:
            duplicate_ids[pair].append(item.relation_id)
            continue
        duplicate_ids[pair] = [item.relation_id]
        adopted.append(item)

    for (source_id, target_id), relation_ids in duplicate_ids.items():
        if len(relation_ids) > 1:
            issues.append(
                GraphIssue(
                    code=DUPLICATE_EDGE,
                    concept_ids=(source_id, target_id),
                    relation_ids=tuple(relation_ids),
                    message=(
                        f"概念 {source_id} → {target_id} 的先修关系重复："
                        f"{list(relation_ids)}"
                    ),
                )
            )

    # 构建邻接表
    adjacency_lists: dict[int, list[int]] = {node.concept_id: [] for node in data.nodes}
    for item in adopted:
        adjacency_lists[item.source_id].append(item.target_id)
    adjacency: dict[int, tuple[int, ...]] = {
        concept_id: tuple(targets) for concept_id, targets in adjacency_lists.items()
    }

    for cycle_path in _find_cycles(adjacency, tuple(node_index)):
        issues.append(
            GraphIssue(
                code=CYCLE,
                concept_ids=tuple(dict.fromkeys(cycle_path)),
                message=f"依赖成环：{' → '.join(str(i) for i in cycle_path)}",
                cycle_path=cycle_path,
            )
        )

    if issues:
        raise GraphDataError(issues)

    prerequisites: dict[int, tuple[int, ...]] = {node.concept_id: () for node in data.nodes}
    successors: dict[int, tuple[int, ...]] = {node.concept_id: () for node in data.nodes}
    prerequisite_lists: dict[int, list[int]] = {
        node.concept_id: [] for node in data.nodes
    }
    successor_lists: dict[int, list[int]] = {node.concept_id: [] for node in data.nodes}
    touched: set[int] = set()
    for item in adopted:
        successor_lists[item.source_id].append(item.target_id)
        prerequisite_lists[item.target_id].append(item.source_id)
        touched.add(item.source_id)
        touched.add(item.target_id)
    for concept_id in node_index:
        prerequisites[concept_id] = tuple(prerequisite_lists[concept_id])
        successors[concept_id] = tuple(successor_lists[concept_id])

    return GraphSnapshot(
        version_id=data.version_id,
        content_hash=data.content_hash,
        nodes=tuple(data.nodes),
        edges=tuple(adopted),
        node_index=MappingProxyType(node_index),
        prerequisites=MappingProxyType(prerequisites),
        successors=MappingProxyType(successors),
        isolated_concept_ids=tuple(
            node.concept_id for node in data.nodes if node.concept_id not in touched
        ),
    )


def _find_cycles(
    adjacency: Mapping[int, tuple[int, ...]],
    node_ids: Sequence[int],
) -> tuple[tuple[int, ...], ...]:
    """迭代式 DFS 找回边，按首次发现顺序返回闭合环链。"""
    colour: dict[int, int] = dict.fromkeys(node_ids, _WHITE)
    found: list[tuple[int, ...]] = []
    for root in node_ids:
        if colour[root] != _WHITE:
            continue
        colour[root] = _GREY
        path = [root]
        stack: list[tuple[int, Iterator[int]]] = [(root, iter(adjacency[root]))]
        while stack:
            current, pending = stack[-1]
            descended = False
            for following in pending:
                if colour[following] == _WHITE:
                    colour[following] = _GREY
                    path.append(following)
                    stack.append((following, iter(adjacency[following])))
                    descended = True
                    break
                if colour[following] == _GREY:
                    found.append((*path[path.index(following) :], following))
            if not descended:
                colour[current] = _BLACK
                path.pop()
                stack.pop()
    return tuple(found)
