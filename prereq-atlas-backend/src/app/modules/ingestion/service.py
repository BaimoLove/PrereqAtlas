"""T08 导入用例：解析两份 CSV、暂存原始记录并生成报告。

解析失败也会保留一个 FAILED 批次，让客户端可凭 422 响应中的
``batch_id`` 查询错误明细；成功批次与原始记录在同一事务内提交。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.common.contracts import ErrorCode, ImportStatus
from app.common.http_contracts import ImportIssue, ImportRawItem, ImportReport
from app.common.time import now_beijing
from app.infra.errors import AppError
from app.modules.ingestion import repository
from app.modules.ingestion.models import ImportBatch
from app.modules.ingestion.parser import ImportIssue as ParserIssue
from app.modules.ingestion.parser import ParsedImport, parse_import_files


def _as_http_issue(issue: ParserIssue) -> ImportIssue:
    return ImportIssue(
        file_kind=issue.file_kind,
        logical_record_no=issue.logical_record_no,
        source_key=issue.source_key,
        code=issue.code,
        message=issue.message,
    )


def _report(
    batch: ImportBatch,
    parsed: ParsedImport,
    errors: list[ImportIssue],
    warnings: list[ImportIssue],
    *,
    duplicate_of: int | None,
) -> ImportReport:
    summary: dict[str, Any] = dict(parsed.mapping_summary)
    if duplicate_of is not None:
        summary["duplicate_of"] = str(duplicate_of)
    return ImportReport(
        batch_id=str(batch.id),
        status=ImportStatus(batch.status),
        node_sha256=parsed.node_sha256,
        edge_sha256=parsed.edge_sha256,
        source_note=batch.source_note,
        counts=dict(parsed.counts),
        errors=errors,
        warnings=warnings,
        mapping_summary=summary,
    )


def import_files(
    session: Session,
    *,
    operator_id: int,
    node_bytes: bytes,
    edge_bytes: bytes,
    source_note: str | None,
) -> ImportReport:
    """解析并暂存一次导入；解析失败时提交 FAILED 批次后抛 422。"""
    parsed = parse_import_files(node_bytes, edge_bytes)
    errors = [_as_http_issue(issue) for issue in parsed.errors]
    warnings = [_as_http_issue(issue) for issue in parsed.warnings]
    duplicate = repository.find_duplicate_batch(
        session, parsed.node_sha256, parsed.edge_sha256
    )
    if duplicate is not None:
        warnings.append(
            ImportIssue(
                file_kind="batch",
                logical_record_no=1,
                code="DUPLICATE_BATCH",
                message=(
                    f"与批次 {duplicate.id} 的两个源文件 SHA-256 相同；"
                    "已创建独立批次，未覆盖原批次"
                ),
            )
        )
    finished_at = now_beijing().replace(tzinfo=None)

    if errors:
        batch = repository.create_batch(
            session,
            operator_id=operator_id,
            source_note=source_note,
            node_sha256=parsed.node_sha256,
            edge_sha256=parsed.edge_sha256,
            status=ImportStatus.FAILED.value,
            finished_at=finished_at,
        )
        failed_report = _report(
            batch,
            parsed,
            errors,
            warnings,
            duplicate_of=duplicate.id if duplicate is not None else None,
        )
        batch.report_json = failed_report.model_dump(mode="json")
        session.commit()
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "导入文件解析失败",
            {
                "batch_id": str(batch.id),
                "errors": [issue.model_dump(mode="json") for issue in errors],
            },
        )

    batch = repository.create_batch(
        session,
        operator_id=operator_id,
        source_note=source_note,
        node_sha256=parsed.node_sha256,
        edge_sha256=parsed.edge_sha256,
        status=ImportStatus.STAGED.value,
        finished_at=finished_at,
    )
    repository.add_raw_nodes(session, batch.id, parsed.nodes)
    repository.add_raw_edges(session, batch.id, parsed.edges)
    report = _report(
        batch,
        parsed,
        errors,
        warnings,
        duplicate_of=duplicate.id if duplicate is not None else None,
    )
    batch.report_json = report.model_dump(mode="json")
    session.commit()
    return report


def get_import_report(
    session: Session, batch_id: str, *, page: int, page_size: int
) -> ImportReport:
    batch = repository.get_batch(session, int(batch_id))
    if batch is None:
        raise AppError(ErrorCode.NOT_FOUND, "导入批次不存在")
    stored = dict(batch.report_json or {})
    offset = (page - 1) * page_size
    rows = repository.list_raw_items(session, batch.id, offset=offset, limit=page_size)
    return ImportReport(
        batch_id=str(batch.id),
        status=ImportStatus(batch.status),
        node_sha256=batch.node_sha256,
        edge_sha256=batch.edge_sha256,
        source_note=batch.source_note,
        counts=dict(stored.get("counts") or {}),
        errors=[ImportIssue.model_validate(item) for item in stored.get("errors") or []],
        warnings=[ImportIssue.model_validate(item) for item in stored.get("warnings") or []],
        mapping_summary=dict(stored.get("mapping_summary") or {}),
        items=[
            ImportRawItem(
                file_kind=row.file_kind,
                source_key=row.source_key,
                logical_record_no=row.logical_record_no,
                raw=row.raw,
            )
            for row in rows
        ],
        page=page,
        page_size=page_size,
        total=repository.count_raw_items(session, batch.id),
    )
