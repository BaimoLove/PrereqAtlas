"""T10～T11 管理端图谱版本与草稿入口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_session, require_admin
from app.common.contracts import Actor, IdStr
from app.common.http_contracts import (
    AcceptImportRequest,
    AcceptImportResponse,
    BatchReviewResponse,
    CreateDraftRequest,
    DraftCreateResponse,
    DraftDetail,
    RelationBatchReviewRequest,
    RelationCreateRequest,
    RelationDeleteRequest,
    RelationMutationResponse,
    RelationUpdateRequest,
    RevisionResponse,
    VersionListQuery,
    VersionListResponse,
)
from app.modules.graph_admin import service

router = APIRouter(tags=["graph_admin"])


@router.get("/admin/graph/versions", summary="管理端图谱版本列表")
def list_versions(
    query: Annotated[VersionListQuery, Query()],
    actor: Annotated[Actor, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session, scope="function")],
) -> VersionListResponse:
    """按状态过滤，按创建时间与主键倒序稳定分页，并标出当前生效版本。"""
    return service.list_versions(
        session,
        actor=actor,
        state=query.state,
        page=query.page,
        page_size=query.page_size,
    )


@router.post("/admin/graph/drafts", status_code=201, summary="创建空草稿或复制发布版")
def create_draft(
    request: CreateDraftRequest,
    actor: Annotated[Actor, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session, scope="function")],
) -> DraftCreateResponse:
    """不带 base_version_id 创建空草稿；带上则整体复制该发布版的内容与关系。"""
    return service.create_draft(session, actor=actor, request=request)


@router.get("/admin/graph/drafts/{draft_id}", summary="草稿内容、审核与校验摘要")
def get_draft(
    draft_id: IdStr,
    actor: Annotated[Actor, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session, scope="function")],
) -> DraftDetail:
    return service.get_draft(session, draft_id, actor=actor)


@router.post("/admin/graph/drafts/{draft_id}/imports", summary="接收已暂存批次到草稿")
def accept_import(
    draft_id: IdStr,
    request: AcceptImportRequest,
    actor: Annotated[Actor, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session, scope="function")],
) -> AcceptImportResponse:
    """接收一个 STAGED 批次：写入章节/概念与 PENDING 候选关系，并把批次置为 CONSUMED。"""
    return service.accept_import(session, draft_id, actor=actor, request=request)

