"""T08 管理员导入 HTTP 入口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from app.api.dependencies import get_session, require_admin
from app.common.contracts import (
    IMPORT_MAX_BYTES,
    SOURCE_NOTE_MAX,
    Actor,
    ErrorCode,
    IdStr,
)
from app.common.http_contracts import ImportDetailQuery, ImportReport
from app.infra.errors import AppError
from app.modules.ingestion import service

router = APIRouter(tags=["ingestion"])


@router.post("/admin/imports", status_code=201, summary="上传并暂存原始图谱文件")
def create_import(
    session: Annotated[Session, Depends(get_session, scope="function")],
    actor: Annotated[Actor, Depends(require_admin)],
    node_file: Annotated[UploadFile, File(description="节点 CSV")],
    edge_file: Annotated[UploadFile, File(description="边 CSV")],
    source_note: Annotated[str | None, Form(max_length=SOURCE_NOTE_MAX)] = None,
) -> ImportReport:
    """接收两份 CSV，解析并暂存；解析失败返回 422 且可凭 batch_id 查报告。"""
    node_bytes = node_file.file.read(IMPORT_MAX_BYTES + 1)
    edge_bytes = edge_file.file.read(IMPORT_MAX_BYTES + 1)
    if len(node_bytes) > IMPORT_MAX_BYTES or len(edge_bytes) > IMPORT_MAX_BYTES:
        raise AppError(ErrorCode.PAYLOAD_TOO_LARGE, "导入文件超过 10 MiB 上限")
    note = source_note.strip() if source_note is not None else None
    return service.import_files(
        session,
        operator_id=actor.user_id,
        node_bytes=node_bytes,
        edge_bytes=edge_bytes,
        source_note=note or None,
    )


@router.get("/admin/imports/{batch_id}", summary="查询导入报告与明细分页")
def get_import(
    batch_id: IdStr,
    query: Annotated[ImportDetailQuery, Query()],
    actor: Annotated[Actor, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session, scope="function")],
) -> ImportReport:
    return service.get_import_report(
        session, batch_id, page=query.page, page_size=query.page_size
    )
