"""T10～T12 管理端图谱版本、草稿与关系入口。"""

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
    ValidateDraftRequest,
    ValidationReport,
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


@router.post(
    "/admin/graph/drafts/{draft_id}/validate",
    summary="校验草稿并记录可发布 revision",
)
def validate_draft(
    draft_id: IdStr,
    request: ValidateDraftRequest,
    actor: Annotated[Actor, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session, scope="function")],
) -> ValidationReport:
    """核对引用、DAG 与审核完备性；未通过时返回 422 与定位报告，不写入任何结论。"""
    return service.validate_draft(session, draft_id, actor=actor, request=request)


@router.post(
    "/admin/graph/drafts/{draft_id}/relations",
    status_code=201,
    summary="在草稿内新增一条先修关系",
)
def create_relation(
    draft_id: IdStr,
    request: RelationCreateRequest,
    actor: Annotated[Actor, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session, scope="function")],
) -> RelationMutationResponse:
    return service.create_relation(session, draft_id, actor=actor, request=request)


@router.post(
    "/admin/graph/drafts/{draft_id}/relations/review",
    summary="批量给出候选关系审核结论",
)
def batch_review_relations(
    draft_id: IdStr,
    request: RelationBatchReviewRequest,
    actor: Annotated[Actor, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session, scope="function")],
) -> BatchReviewResponse:
    """整批共用一次 revision；任一条不存在或不属于本草稿则整批失败。"""
    return service.batch_review(session, draft_id, actor=actor, request=request)


@router.post(
    "/admin/graph/drafts/{draft_id}/relations/{relation_id}/update",
    summary="单条审核或修改先修关系",
)
def update_relation(
    draft_id: IdStr,
    relation_id: IdStr,
    request: RelationUpdateRequest,
    actor: Annotated[Actor, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session, scope="function")],
) -> RelationMutationResponse:
    """除端点/必要性变更外不做隐式改写；改端点自动退回 PENDING。"""
    return service.update_relation(
        session, draft_id, relation_id, actor=actor, request=request
    )


@router.post(
    "/admin/graph/drafts/{draft_id}/relations/{relation_id}/delete",
    summary="删除草稿内的一条先修关系",
)
def delete_relation(
    draft_id: IdStr,
    relation_id: IdStr,
    request: RelationDeleteRequest,
    actor: Annotated[Actor, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session, scope="function")],
) -> RevisionResponse:
    return service.delete_relation(
        session, draft_id, relation_id, actor=actor, request=request
    )
