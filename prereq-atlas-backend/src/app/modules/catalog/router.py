"""登录后的固定发布版查询入口。"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import (
    get_active_context,
    get_detail_context,
    get_query_context,
    get_version_context,
    search_query,
)
from app.common.contracts import ErrorCode, IdStr, parse_id
from app.common.http_contracts import (
    ActiveGraphSummary,
    ConceptCard,
    ConceptSearchQuery,
    GraphResponse,
)
from app.infra.errors import AppError
from app.modules.catalog.context import PublishedReadContext
from app.modules.catalog.queries import concept_card, query_graph, search_concepts
from app.modules.catalog.schema import ConceptListResponse

router = APIRouter(tags=["catalog"])


@router.get("/graph/active", summary="当前生效发布版摘要")
def active_graph(
    context: Annotated[PublishedReadContext, Depends(get_active_context)],
) -> ActiveGraphSummary:
    if context.active_revision is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "尚无生效图谱")
    return ActiveGraphSummary(
        version_id=str(context.version_id),
        label=context.label,
        content_hash=context.content_hash,
        active_revision=context.active_revision,
    )


@router.get("/graph/versions/{version_id}", summary="固定发布版本全量图")
def version_graph(
    context: Annotated[PublishedReadContext, Depends(get_version_context)],
) -> GraphResponse:
    return query_graph(context.data)


@router.get("/concepts", summary="固定版本概念检索与分页")
def concepts(
    query: Annotated[ConceptSearchQuery, Depends(search_query)],
    context: Annotated[PublishedReadContext, Depends(get_query_context)],
) -> ConceptListResponse:
    return search_concepts(context.data, query)


@router.get("/concepts/{concept_id}", summary="固定版本知识卡与直接先修/后继")
def knowledge_card(
    concept_id: IdStr,
    context: Annotated[PublishedReadContext, Depends(get_detail_context)],
) -> ConceptCard:
    return concept_card(context.data, parse_id(concept_id))
