"""M08 的受控公开面：供 M06 读取接收候选并回写批次状态。

只有这里可以被 ``graph_admin`` 导入；解析、暂存与报告仍是本模块内部实现。
接收方拿到的是不可变候选记录，不能借此修改原始来源值——``import_raw_*``
的原值只由 T08 写入。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.common.contracts import ErrorCode, ImportStatus
from app.infra.errors import AppError
from app.modules.ingestion import repository


@dataclass(frozen=True, slots=True)
class NodeCandidate:
    """一个来源节点及其规范化候选内容；``label_zh`` 缺失表示来源数据不完整。"""

    source_key: str
    label_zh: str | None
    label_en: str | None
    chapter_code: str | None
    chapter_name: str | None
    chapter_order: int | None
    node_type: str | None


@dataclass(frozen=True, slots=True)
class EdgeCandidate:
    """一条来源边；``structural_type`` 与 ``source_strength`` 都是来源原值。"""

    source_edge_key: str
    source_node_key: str
    target_node_key: str
    structural_type: str | None
    source_strength: Decimal | None


@dataclass(frozen=True, slots=True)
class ImportCandidates:
    """一个批次的完整候选内容；状态决定它能否被接收。"""

    batch_id: int
    status: str
    node_sha256: str
    source_note: str | None
    nodes: tuple[NodeCandidate, ...]
    edges: tuple[EdgeCandidate, ...]


def _chapter_order(value: Any) -> int | None:
    """来源模块顺序只作展示顺序；无法解析时不猜测，留空由调用方决定。"""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        order = int(text)
    except ValueError:
        return None
    return order if order >= 0 else None


def read_candidates(session: Session, batch_id: int) -> ImportCandidates | None:
    """读取批次的候选节点与边；批次不存在返回 ``None``，状态由调用方判定。"""
    batch = repository.get_batch(session, batch_id)
    if batch is None:
        return None
    nodes = tuple(
        NodeCandidate(
            source_key=row.source_node_key,
            label_zh=(row.raw_json or {}).get("label_zh") or None,
            label_en=(row.raw_json or {}).get("label_en") or None,
            chapter_code=(row.raw_json or {}).get("module_id") or None,
            chapter_name=(row.raw_json or {}).get("module_name") or None,
            chapter_order=_chapter_order((row.raw_json or {}).get("module_order")),
            node_type=(row.raw_json or {}).get("node_type") or None,
        )
        for row in repository.list_raw_nodes(session, batch_id)
    )
    edges = tuple(
        EdgeCandidate(
            source_edge_key=row.source_edge_key,
            source_node_key=row.source_node_key,
            target_node_key=row.target_node_key,
            structural_type=row.structural_type,
            source_strength=row.source_strength,
        )
        for row in repository.list_raw_edges(session, batch_id)
    )
    return ImportCandidates(
        batch_id=batch.id,
        status=batch.status,
        node_sha256=batch.node_sha256,
        source_note=batch.source_note,
        nodes=nodes,
        edges=edges,
    )


def record_node_mapping(
    session: Session, batch_id: int, mapping: Mapping[str, int]
) -> int:
    """把来源节点编号映射到概念稳定身份；原始值与逻辑记录号保持不变。"""
    if not mapping:
        return 0
    return repository.update_node_mapping(session, batch_id, mapping)


def mark_consumed(
    session: Session, batch_id: int, *, summary: Mapping[str, Any]
) -> None:
    """STAGED → CONSUMED，并把接收结果附到批次报告的映射摘要下。"""
    batch = repository.get_batch(session, batch_id)
    if batch is None:
        raise AppError(ErrorCode.NOT_FOUND, "导入批次不存在")
    if batch.status != ImportStatus.STAGED.value:
        raise AppError(
            ErrorCode.CONFLICT,
            "导入批次当前状态不可接收",
            {
                "resource_type": "import_batch",
                "ids": [str(batch_id)],
                "status": batch.status,
            },
        )
    report = dict(batch.report_json or {})
    mapping_summary = dict(report.get("mapping_summary") or {})
    mapping_summary["accept"] = dict(summary)
    report["mapping_summary"] = mapping_summary
    batch.report_json = report
    batch.status = ImportStatus.CONSUMED.value
    session.flush()


__all__ = (
    "EdgeCandidate",
    "ImportCandidates",
    "NodeCandidate",
    "mark_consumed",
    "read_candidates",
    "record_node_mapping",
)
