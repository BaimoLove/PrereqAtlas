"""T07 的 CSV 纯解析入口。

``parse_import_files`` 只接收字节流：不读文件、不访问网络、不连数据库。
两份 CSV 中所有以 ``#`` 开头的说明记录都不计入逻辑记录号。
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.common.contracts import IMPORT_MAX_BYTES, IMPORT_MAX_RECORDS, URL_MAX
from app.modules.ingestion.mapping import normalize_edge, normalize_node

NODE_COLUMNS = (
    "node_id",
    "name",
    "label_en",
    "label_zh",
    "module_id",
    "module_name",
    "module_order",
    "node_type",
    "graph_level",
    "direct_prereq_count",
    "all_prereq_count",
    "direct_successor_count",
    "all_successor_count",
    "is_root",
    "is_leaf",
    "is_isolated",
    "wikipedia_url",
    "source",
)
EDGE_COLUMNS = (
    "edge_id",
    "source_id",
    "source_name",
    "target_id",
    "target_name",
    "relation",
    "edge_type",
    "strength",
    "redundant_path_len",
    "source_module_id",
    "target_module_id",
    "evidence",
)


@dataclass(frozen=True, slots=True)
class ImportIssue:
    """解析问题；``logical_record_no`` 是不含表头与说明行的数据记录序号。"""

    file_kind: str
    logical_record_no: int
    code: str
    message: str
    source_key: str | None = None


@dataclass(frozen=True, slots=True)
class RawNodeRecord:
    source_key: str
    logical_record_no: int
    values: dict[str, str]
    candidate: dict[str, str | None]


@dataclass(frozen=True, slots=True)
class RawEdgeRecord:
    source_key: str
    logical_record_no: int
    values: dict[str, str]
    source_node_key: str
    target_node_key: str
    candidate: dict[str, object]


@dataclass(frozen=True, slots=True)
class ParsedImport:
    nodes: tuple[RawNodeRecord, ...]
    edges: tuple[RawEdgeRecord, ...]
    errors: tuple[ImportIssue, ...]
    warnings: tuple[ImportIssue, ...]
    counts: dict[str, int]
    node_sha256: str
    edge_sha256: str
    mapping_summary: dict[str, object]


_NON_UTF8_BOMS = (
    b"\xff\xfe\x00\x00",
    b"\x00\x00\xfe\xff",
    b"\xff\xfe",
    b"\xfe\xff",
)

# 单个字段可能占满整个上限文件；csv 默认 128 KiB 会在上限内误报。
csv.field_size_limit(IMPORT_MAX_BYTES + 1024)


def _decode(
    data: bytes, file_kind: str, errors: list[ImportIssue]
) -> str | None:
    """只接受 UTF-8 / UTF-8 BOM；空文件、超限与未知编码都明确失败。"""
    if not data:
        errors.append(
            ImportIssue(file_kind, 1, "FILE_EMPTY", "文件为空")
        )
        return None
    if len(data) > IMPORT_MAX_BYTES:
        errors.append(
            ImportIssue(
                file_kind,
                1,
                "FILE_TOO_LARGE",
                f"文件超过 {IMPORT_MAX_BYTES} 字节",
            )
        )
        return None
    if any(data.startswith(bom) for bom in _NON_UTF8_BOMS):
        errors.append(
            ImportIssue(
                file_kind, 1, "ENCODING_INVALID", "只接受 UTF-8 或 UTF-8 BOM"
            )
        )
        return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        errors.append(
            ImportIssue(
                file_kind, 1, "ENCODING_INVALID", "只接受 UTF-8 或 UTF-8 BOM"
            )
        )
        return None


def _split_records(text: str) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """返回表头与 ``(逻辑记录号, 字段)``；说明行与空行不占记录号。"""
    lines = [
        line
        for line in text.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not lines:
        return [], []
    reader = csv.reader(lines)
    header = next(reader)
    return header, list(enumerate(reader, start=1))


def _is_valid_url(url: str) -> bool:
    """只接受 http/https 绝对 URL；空值表示没有链接。不做任何网络访问。"""
    if not url:
        return True
    if len(url) > URL_MAX:
        return False
    if any(
        character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F
        for character in url
    ):
        return False
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
    except ValueError:
        return False
    return parts.scheme in {"http", "https"} and bool(hostname)


def _parse_nodes(
    text: str, errors: list[ImportIssue]
) -> tuple[RawNodeRecord, ...]:
    header, rows = _split_records(text)
    if header != list(NODE_COLUMNS):
        errors.append(
            ImportIssue("node", 1, "HEADER_MISSING", "表头缺失或列不匹配")
        )
        return ()
    nodes = []
    seen: set[str] = set()
    for record_no, row in rows:
        if len(row) != len(NODE_COLUMNS):
            errors.append(
                ImportIssue(
                    "node",
                    record_no,
                    "ROW_FIELD_COUNT",
                    f"期望 {len(NODE_COLUMNS)} 列，实际 {len(row)} 列",
                )
            )
            continue
        values = dict(zip(header, row, strict=True))
        source_key = values["node_id"]
        if not source_key.strip():
            errors.append(
                ImportIssue("node", record_no, "MISSING_ID", "缺少节点编号")
            )
            continue
        if source_key in seen:
            errors.append(
                ImportIssue(
                    "node",
                    record_no,
                    "DUPLICATE_ID",
                    f"节点编号重复：{source_key}",
                    source_key,
                )
            )
            continue
        if not _is_valid_url(values["wikipedia_url"]):
            errors.append(
                ImportIssue(
                    "node",
                    record_no,
                    "INVALID_URL",
                    f"非法 URL：{values['wikipedia_url'][:120]}",
                    source_key,
                )
            )
            continue
        seen.add(source_key)
        nodes.append(
            RawNodeRecord(
                source_key=source_key,
                logical_record_no=record_no,
                values=values,
                candidate=normalize_node(values),
            )
        )
    if len(nodes) > IMPORT_MAX_RECORDS:
        errors.append(
            ImportIssue(
                "node",
                nodes[IMPORT_MAX_RECORDS].logical_record_no,
                "RECORD_LIMIT_EXCEEDED",
                f"有效记录超过 {IMPORT_MAX_RECORDS} 条",
            )
        )
        return ()
    return tuple(nodes)


def _parse_edges(
    text: str, errors: list[ImportIssue]
) -> tuple[RawEdgeRecord, ...]:
    header, rows = _split_records(text)
    if header != list(EDGE_COLUMNS):
        errors.append(
            ImportIssue("edge", 1, "HEADER_MISSING", "表头缺失或列不匹配")
        )
        return ()
    edges = []
    seen: set[str] = set()
    for record_no, row in rows:
        if len(row) != len(EDGE_COLUMNS):
            errors.append(
                ImportIssue(
                    "edge",
                    record_no,
                    "ROW_FIELD_COUNT",
                    f"期望 {len(EDGE_COLUMNS)} 列，实际 {len(row)} 列",
                )
            )
            continue
        values = dict(zip(header, row, strict=True))
        source_key = values["edge_id"]
        if not source_key.strip():
            errors.append(
                ImportIssue("edge", record_no, "MISSING_ID", "缺少边编号")
            )
            continue
        if source_key in seen:
            errors.append(
                ImportIssue(
                    "edge",
                    record_no,
                    "DUPLICATE_ID",
                    f"边编号重复：{source_key}",
                    source_key,
                )
            )
            continue
        seen.add(source_key)
        edges.append(
            RawEdgeRecord(
                source_key=source_key,
                logical_record_no=record_no,
                values=values,
                source_node_key=values["source_id"],
                target_node_key=values["target_id"],
                candidate=normalize_edge(values),
            )
        )
    if len(edges) > IMPORT_MAX_RECORDS:
        errors.append(
            ImportIssue(
                "edge",
                edges[IMPORT_MAX_RECORDS].logical_record_no,
                "RECORD_LIMIT_EXCEEDED",
                f"有效记录超过 {IMPORT_MAX_RECORDS} 条",
            )
        )
        return ()
    return tuple(edges)


def _validate_edges(
    edges: tuple[RawEdgeRecord, ...],
    nodes: tuple[RawNodeRecord, ...],
    errors: list[ImportIssue],
    warnings: list[ImportIssue],
) -> tuple[RawEdgeRecord, ...]:
    """丢弃端点缺失的边；端点存在但名称不一致时保留边并告警。"""
    node_names = {node.source_key: node.values["name"] for node in nodes}
    kept = []
    for edge in edges:
        missing = [
            key
            for key in (edge.source_node_key, edge.target_node_key)
            if key not in node_names
        ]
        if missing:
            errors.append(
                ImportIssue(
                    "edge",
                    edge.logical_record_no,
                    "MISSING_ENDPOINT",
                    f"端点不存在：{', '.join(missing)}",
                    edge.source_key,
                )
            )
            continue
        for key, name in (
            (edge.source_node_key, edge.values["source_name"]),
            (edge.target_node_key, edge.values["target_name"]),
        ):
            if name != node_names[key]:
                warnings.append(
                    ImportIssue(
                        "edge",
                        edge.logical_record_no,
                        "ENDPOINT_NAME_MISMATCH",
                        f"端点 {key} 名称不一致：{name} != {node_names[key]}",
                        edge.source_key,
                    )
                )
        kept.append(edge)
    return tuple(kept)


def _counts(
    nodes: tuple[RawNodeRecord, ...], edges: tuple[RawEdgeRecord, ...]
) -> dict[str, int]:
    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "direct_count": sum(
            1 for edge in edges if edge.values["edge_type"] == "direct"
        ),
        "transitive_count": sum(
            1 for edge in edges if edge.values["edge_type"] == "transitive"
        ),
        "chapter_count": len({node.values["module_id"] for node in nodes}),
        "isolated_count": sum(
            1 for node in nodes if node.values["is_isolated"] == "1"
        ),
    }


def parse_import_files(node_bytes: bytes, edge_bytes: bytes) -> ParsedImport:
    """解析节点/边两份 CSV 字节流；说明行不计，方向保持 source → target。"""
    errors: list[ImportIssue] = []
    warnings: list[ImportIssue] = []
    node_text = _decode(node_bytes, "node", errors)
    edge_text = _decode(edge_bytes, "edge", errors)
    nodes = _parse_nodes(node_text, errors) if node_text is not None else ()
    edges = _parse_edges(edge_text, errors) if edge_text is not None else ()
    if node_text is not None and edge_text is not None and not any(
        error.code == "RECORD_LIMIT_EXCEEDED" and error.file_kind == "node"
        for error in errors
    ):
        edges = _validate_edges(edges, nodes, errors, warnings)
    return ParsedImport(
        nodes=nodes,
        edges=edges,
        errors=tuple(errors),
        warnings=tuple(warnings),
        counts=_counts(nodes, edges),
        node_sha256=hashlib.sha256(node_bytes).hexdigest(),
        edge_sha256=hashlib.sha256(edge_bytes).hexdigest(),
        mapping_summary={
            "chapter_codes": sorted({node.values["module_id"] for node in nodes}),
        },
    )
