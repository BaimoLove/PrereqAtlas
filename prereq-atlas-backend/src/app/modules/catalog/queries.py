"""固定版本查询；仅消费应用装配层注入的发布数据，不读取生效指针。"""

from app.common.contracts import ErrorCode, Necessity, ReviewStatus, VersionData, VersionNode
from app.common.http_contracts import (
    ChapterItem,
    ConceptCard,
    ConceptContentOut,
    ConceptSearchQuery,
    GraphEdge,
    GraphNode,
    GraphResponse,
    RelatedConcept,
    ResourceItem,
)
from app.infra.errors import AppError
from app.modules.catalog.schema import ConceptListResponse


def graph_node(node: VersionNode) -> GraphNode:
    return GraphNode(
        concept_id=str(node.concept_id),
        code=node.code,
        content=ConceptContentOut(
            label_zh=node.label_zh,
            label_en=node.label_en,
            chapter_code=node.chapter_code,
            node_type=node.node_type,
            introduction=node.introduction,
            learning_focus=node.learning_focus,
        ),
    )


def query_graph(data: VersionData) -> GraphResponse:
    assert data.content_hash is not None
    return GraphResponse(
        version_id=str(data.version_id),
        content_hash=data.content_hash,
        chapters=[ChapterItem(**c.model_dump()) for c in data.chapters],
        nodes=[graph_node(node) for node in data.nodes],
        edges=[
            GraphEdge(
                relation_id=str(e.relation_id),
                source_id=str(e.source_id),
                target_id=str(e.target_id),
                necessity=e.necessity,
            )
            for e in data.edges
            if e.necessity is Necessity.REQUIRED and e.review_status is ReviewStatus.APPROVED
        ],
    )


def search_concepts(data: VersionData, query: ConceptSearchQuery) -> ConceptListResponse:
    assert data.content_hash is not None
    needle = query.q.casefold() if query.q else None
    nodes = [
        n
        for n in data.nodes
        if (query.chapter is None or n.chapter_code == query.chapter)
        and (
            needle is None
            or any(needle in v.casefold() for v in (n.code, n.label_zh, n.label_en or ""))
        )
    ]
    offset = (query.page - 1) * query.page_size
    return ConceptListResponse(
        version_id=str(data.version_id),
        content_hash=data.content_hash,
        total=len(nodes),
        page=query.page,
        page_size=query.page_size,
        items=[graph_node(n) for n in nodes[offset : offset + query.page_size]],
    )


def concept_card(data: VersionData, concept_id: int) -> ConceptCard:
    nodes = {n.concept_id: n for n in data.nodes}
    node = nodes.get(concept_id)
    if node is None:
        raise AppError(ErrorCode.NOT_FOUND, "概念不属于该发布版本")
    prerequisites: list[RelatedConcept] = []
    successors: list[RelatedConcept] = []
    for edge in data.edges:
        if (
            edge.review_status is not ReviewStatus.APPROVED
            or edge.necessity is not Necessity.REQUIRED
        ):
            continue
        if edge.target_id == concept_id:
            other = nodes[edge.source_id]
            prerequisites.append(
                RelatedConcept(
                    relation_id=str(edge.relation_id),
                    concept_id=str(other.concept_id),
                    code=other.code,
                    label_zh=other.label_zh,
                    necessity=edge.necessity,
                )
            )
        if edge.source_id == concept_id:
            other = nodes[edge.target_id]
            successors.append(
                RelatedConcept(
                    relation_id=str(edge.relation_id),
                    concept_id=str(other.concept_id),
                    code=other.code,
                    label_zh=other.label_zh,
                    necessity=edge.necessity,
                )
            )
    return ConceptCard(
        version_id=str(data.version_id),
        concept_id=str(concept_id),
        code=node.code,
        content=graph_node(node).content,
        resources=[
            ResourceItem(resource_id=str(r.resource_id), title=r.title, url=r.url, source=r.source)
            for r in node.resources
        ],
        prerequisites=prerequisites,
        successors=successors,
    )
