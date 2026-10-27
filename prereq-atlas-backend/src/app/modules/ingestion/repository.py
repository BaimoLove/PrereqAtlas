"""T08 导入批次的持久化访问。

只负责 SQLAlchemy 查询与写入，不解析字节流，也不构造 HTTP 报告。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.ingestion.models import ImportBatch, ImportRawEdge, ImportRawNode
from app.modules.ingestion.parser import RawEdgeRecord, RawNodeRecord


@dataclass(frozen=True, slots=True)
class RawItemRow:
    """一页中的一条原始记录；``raw`` 即入库的 ``raw_json``。"""

    file_kind: Literal["node", "edge"]
    source_key: str
    logical_record_no: int
    raw: dict[str, Any]


def find_duplicate_batch(
    session: Session, node_sha256: str, edge_sha256: str
) -> ImportBatch | None:
    """同哈希的既有成功批次；失败批次不算重复，允许重试。"""
    statement = (
        select(ImportBatch)
        .where(
            ImportBatch.node_sha256 == node_sha256,
            ImportBatch.edge_sha256 == edge_sha256,
            ImportBatch.status != "FAILED",
        )
        .order_by(ImportBatch.id)
        .limit(1)
    )
    return session.scalars(statement).first()


def create_batch(
    session: Session,
    *,
    operator_id: int,
    source_note: str | None,
    node_sha256: str,
    edge_sha256: str,
    status: str,
    finished_at: datetime | None = None,
) -> ImportBatch:
    batch = ImportBatch(
        operator_id=operator_id,
        source_note=source_note,
        node_sha256=node_sha256,
        edge_sha256=edge_sha256,
        status=status,
        finished_at=finished_at,
    )
    session.add(batch)
    session.flush()
    return batch


def add_raw_nodes(session: Session, batch_id: int, nodes: tuple[RawNodeRecord, ...]) -> None:
    session.add_all(
        [
            ImportRawNode(
                batch_id=batch_id,
                source_node_key=node.source_key,
                logical_record_no=node.logical_record_no,
                raw_json=node.values,
                mapping_status="UNMAPPED",
                mapped_concept_id=None,
            )
            for node in nodes
        ]
    )


def _strength(value: str) -> Decimal | None:
    """保留来源强度原值；无法转成定点数时留空，原值仍在 raw_json 中。"""
    text = value.strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def add_raw_edges(session: Session, batch_id: int, edges: tuple[RawEdgeRecord, ...]) -> None:
    session.add_all(
        [
            ImportRawEdge(
                batch_id=batch_id,
                source_edge_key=edge.source_key,
                logical_record_no=edge.logical_record_no,
                source_node_key=edge.source_node_key,
                target_node_key=edge.target_node_key,
                structural_type=edge.values.get("edge_type") or None,
                source_strength=_strength(edge.values.get("strength", "")),
                raw_json=edge.values,
                review_note=None,
            )
            for edge in edges
        ]
    )


def get_batch(session: Session, batch_id: int) -> ImportBatch | None:
    return session.get(ImportBatch, batch_id)


def count_raw_items(session: Session, batch_id: int) -> int:
    node_count = session.scalar(
        select(func.count()).select_from(ImportRawNode).where(ImportRawNode.batch_id == batch_id)
    )
    edge_count = session.scalar(
        select(func.count()).select_from(ImportRawEdge).where(ImportRawEdge.batch_id == batch_id)
    )
    return int(node_count or 0) + int(edge_count or 0)


def list_raw_items(
    session: Session, batch_id: int, *, offset: int, limit: int
) -> list[RawItemRow]:
    """先节点后边、各自按逻辑记录号排序后拼接的一页。"""
    node_total = int(
        session.scalar(
            select(func.count())
            .select_from(ImportRawNode)
            .where(ImportRawNode.batch_id == batch_id)
        )
        or 0
    )
    items: list[RawItemRow] = []
    if offset < node_total and limit > 0:
        nodes = session.scalars(
            select(ImportRawNode)
            .where(ImportRawNode.batch_id == batch_id)
            .order_by(ImportRawNode.logical_record_no)
            .offset(offset)
            .limit(limit)
        ).all()
        items.extend(
            RawItemRow(
                file_kind="node",
                source_key=node.source_node_key,
                logical_record_no=node.logical_record_no,
                raw=node.raw_json,
            )
            for node in nodes
        )
    remaining = limit - len(items)
    if remaining > 0:
        edge_offset = max(0, offset - node_total)
        edges = session.scalars(
            select(ImportRawEdge)
            .where(ImportRawEdge.batch_id == batch_id)
            .order_by(ImportRawEdge.logical_record_no)
            .offset(edge_offset)
            .limit(remaining)
        ).all()
        items.extend(
            RawItemRow(
                file_kind="edge",
                source_key=edge.source_edge_key,
                logical_record_no=edge.logical_record_no,
                raw=edge.raw_json,
            )
            for edge in edges
        )
    return items
