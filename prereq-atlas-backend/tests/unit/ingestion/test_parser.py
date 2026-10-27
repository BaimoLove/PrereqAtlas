"""T07：CSV 纯解析与来源规则。

测试缝隙只有 ``parse_import_files``：输入两份文件的字节流，观察解析出的
逻辑记录、错误/警告、汇总和哈希；不触碰数据库、文件系统或网络。
"""

import csv
import io
import socket
from pathlib import Path
from urllib import request as urlopen_module

import pytest

from app.common.contracts import IMPORT_MAX_BYTES, IMPORT_MAX_RECORDS
from app.modules.ingestion.parser import EDGE_COLUMNS, NODE_COLUMNS, parse_import_files

REPO_ROOT = Path(__file__).resolve().parents[4]


def _real_files() -> tuple[bytes, bytes]:
    return (
        (REPO_ROOT / "datamining_kg_nodes.csv").read_bytes(),
        (REPO_ROOT / "datamining_kg_edges.csv").read_bytes(),
    )


def _csv_bytes(
    header: tuple[str, ...], rows: list[list[str]], *, bom: bool = False
) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    payload = buffer.getvalue().encode("utf-8")
    return (b"\xef\xbb\xbf" + payload) if bom else payload


def _node_row(**overrides: str) -> list[str]:
    values = {
        "node_id": "N001",
        "name": "Arithmetic_mean",
        "label_en": "Arithmetic mean",
        "label_zh": "算术平均数",
        "module_id": "M01",
        "module_name": "统计基础",
        "module_order": "1",
        "node_type": "基础概念",
        "graph_level": "0",
        "direct_prereq_count": "0",
        "all_prereq_count": "0",
        "direct_successor_count": "1",
        "all_successor_count": "1",
        "is_root": "1",
        "is_leaf": "0",
        "is_isolated": "0",
        "wikipedia_url": "https://en.wikipedia.org/wiki/Arithmetic_mean",
        "source": "AL-CPL-dataset/data/data_mining.preqs",
    }
    values.update(overrides)
    return [values[column] for column in NODE_COLUMNS]


def _second_node_row(**overrides: str) -> list[str]:
    values = {
        "node_id": "N002",
        "name": "Median",
        "label_en": "Median",
        "label_zh": "中位数",
        "wikipedia_url": "https://en.wikipedia.org/wiki/Median",
    }
    values.update(overrides)
    return _node_row(**values)


def _edge_row(**overrides: str) -> list[str]:
    values = {
        "edge_id": "E001",
        "source_id": "N001",
        "source_name": "Arithmetic_mean",
        "target_id": "N002",
        "target_name": "Median",
        "relation": "prerequisite_of",
        "edge_type": "direct",
        "strength": "1.0",
        "redundant_path_len": "",
        "source_module_id": "M01",
        "target_module_id": "M01",
        "evidence": "人工标注",
    }
    values.update(overrides)
    return [values[column] for column in EDGE_COLUMNS]


def _edge_bytes(rows: list[list[str]] | None = None) -> bytes:
    return _csv_bytes(EDGE_COLUMNS, rows if rows is not None else [_edge_row()])


def _pair(
    node_rows: list[list[str]] | None = None,
    edge_rows: list[list[str]] | None = None,
    **node_options: bool,
) -> tuple[bytes, bytes]:
    rows = node_rows if node_rows is not None else [_node_row(), _second_node_row()]
    return (
        _csv_bytes(NODE_COLUMNS, rows, **node_options),
        _edge_bytes(edge_rows),
    )


def _test_edge_row_for_generated_nodes() -> list[str]:
    return _edge_row(
        source_id="N00001",
        source_name="Arithmetic_mean",
        target_id="N00002",
        target_name="Arithmetic_mean",
    )


def _node_bytes_of_size(size: int) -> bytes:
    base = _csv_bytes(NODE_COLUMNS, [_node_row(), _second_node_row(source="")])
    filler = size - len(base)
    assert filler >= 0
    return _csv_bytes(
        NODE_COLUMNS, [_node_row(), _second_node_row(source="a" * filler)]
    )


def _edge_bytes_of_size(size: int) -> bytes:
    base = _csv_bytes(EDGE_COLUMNS, [_edge_row(evidence="")])
    filler = size - len(base)
    assert filler >= 0
    return _csv_bytes(EDGE_COLUMNS, [_edge_row(evidence="a" * filler)])


@pytest.mark.parametrize(
    ("size", "accepted"),
    [
        (IMPORT_MAX_BYTES - 1, True),
        (IMPORT_MAX_BYTES, True),
        (IMPORT_MAX_BYTES + 1, False),
    ],
)
def test_byte_limit_boundaries(size: int, accepted: bool) -> None:
    _node_bytes, edge_bytes = _pair()

    result = parse_import_files(_node_bytes_of_size(size), edge_bytes)

    if accepted:
        assert result.errors == ()
        assert len(result.nodes) == 2
    else:
        assert [error.code for error in result.errors] == ["FILE_TOO_LARGE"]
        assert result.nodes == ()


def test_edge_file_byte_limit_is_enforced() -> None:
    node_bytes, _ = _pair()

    result = parse_import_files(
        node_bytes, _edge_bytes_of_size(IMPORT_MAX_BYTES + 1)
    )

    assert [error.code for error in result.errors] == ["FILE_TOO_LARGE"]
    assert result.edges == ()


@pytest.mark.parametrize(
    ("count", "accepted"),
    [
        (IMPORT_MAX_RECORDS - 1, True),
        (IMPORT_MAX_RECORDS, True),
        (IMPORT_MAX_RECORDS + 1, False),
    ],
)
def test_record_limit_boundaries(count: int, accepted: bool) -> None:
    rows = [_node_row(node_id=f"N{index:05d}") for index in range(1, count + 1)]

    result = parse_import_files(
        _csv_bytes(NODE_COLUMNS, rows), _edge_bytes([_test_edge_row_for_generated_nodes()])
    )

    if accepted:
        assert result.errors == ()
        assert len(result.nodes) == count
    else:
        assert [error.code for error in result.errors] == [
            "RECORD_LIMIT_EXCEEDED"
        ]
        assert result.nodes == ()


@pytest.mark.parametrize(
    "url",
    [
        "not-a-url",
        "ftp://example.com/page",
        "https://",
        "https://exa mple.com/page",
        "https://example.com/\x00",
        "https://example.com/" + "a" * 1000,
    ],
)
def test_invalid_url_is_reported_and_node_rejected(url: str) -> None:
    node_rows = [_node_row(wikipedia_url=url), _second_node_row()]

    result = parse_import_files(_csv_bytes(NODE_COLUMNS, node_rows), _edge_bytes())

    node_errors = [error for error in result.errors if error.file_kind == "node"]
    assert [
        (error.code, error.logical_record_no, error.source_key)
        for error in node_errors
    ] == [("INVALID_URL", 1, "N001")]
    assert [node.source_key for node in result.nodes] == ["N002"]


def test_url_validation_never_opens_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("URL 解析不允许访问网络")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(urlopen_module, "urlopen", _blocked)

    node_rows = [
        _node_row(),
        _second_node_row(
            wikipedia_url="https://zh.wikipedia.org/wiki/%E4%B8%AD%E4%BD%8D%E6%95%B0"
        ),
    ]

    result = parse_import_files(_csv_bytes(NODE_COLUMNS, node_rows), _edge_bytes())

    assert result.errors == ()
    assert len(result.nodes) == 2


def test_source_derived_fields_are_preserved_but_not_planning_fields() -> None:
    node_rows = [
        _node_row(
            graph_level="3",
            direct_prereq_count="7",
            all_prereq_count="9",
            is_root="0",
            is_leaf="1",
            source="AL-CPL-dataset/data/data_mining.preqs",
        ),
        _second_node_row(),
    ]

    result = parse_import_files(_csv_bytes(NODE_COLUMNS, node_rows), _edge_bytes())

    raw = result.nodes[0]
    assert raw.values["graph_level"] == "3"
    assert raw.values["direct_prereq_count"] == "7"
    assert raw.values["all_prereq_count"] == "9"
    assert raw.values["is_root"] == "0"
    assert raw.values["is_leaf"] == "1"
    assert raw.values["source"] == "AL-CPL-dataset/data/data_mining.preqs"
    assert set(raw.candidate) == {
        "source_key",
        "label_zh",
        "label_en",
        "chapter_code",
        "node_type",
    }
    assert raw.candidate["chapter_code"] == "M01"


def test_path_like_values_never_touch_filesystem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[str] = []
    real_open = open
    real_path_open = Path.open
    real_read_bytes = Path.read_bytes

    def _guard_open(file: object, *args: object, **kwargs: object) -> object:
        opened.append(str(file))
        return real_open(file, *args, **kwargs)  # type: ignore[arg-type]

    def _guard_path_open(self: Path, *args: object, **kwargs: object) -> object:
        opened.append(str(self))
        return real_path_open(self, *args, **kwargs)  # type: ignore[arg-type]

    def _guard_read_bytes(self: Path) -> bytes:
        opened.append(str(self))
        return real_read_bytes(self)

    monkeypatch.setattr("builtins.open", _guard_open)
    monkeypatch.setattr(Path, "open", _guard_path_open)
    monkeypatch.setattr(Path, "read_bytes", _guard_read_bytes)

    node_rows = [
        _node_row(source="../../etc/passwd"),
        _second_node_row(source="C:\\Windows\\win.ini"),
    ]
    edge_rows = [_edge_row(evidence="../../../secret/edges.csv")]

    result = parse_import_files(
        _csv_bytes(NODE_COLUMNS, node_rows), _edge_bytes(edge_rows)
    )

    assert result.errors == ()
    assert opened == []
    assert [node.values["source"] for node in result.nodes] == [
        "../../etc/passwd",
        "C:\\Windows\\win.ini",
    ]


def test_real_csv_counts_ignore_comment_record() -> None:
    node_bytes, edge_bytes = _real_files()

    result = parse_import_files(node_bytes, edge_bytes)

    assert result.errors == ()
    assert len(result.nodes) == 120
    assert len(result.edges) == 292
    assert result.counts["node_count"] == 120
    assert result.counts["edge_count"] == 292
    assert result.counts["isolated_count"] == 30
    assert [node.logical_record_no for node in result.nodes] == list(range(1, 121))
    assert all(not node.source_key.startswith("#") for node in result.nodes)


def test_utf8_bom_is_accepted() -> None:
    node_bytes, edge_bytes = _pair(bom=True)

    result = parse_import_files(node_bytes, edge_bytes)

    assert result.errors == ()
    assert len(result.nodes) == 2


def test_unknown_encoding_fails_explicitly() -> None:
    node_bytes, edge_bytes = _pair()
    payload = node_bytes.decode("utf-8").encode("gbk")

    result = parse_import_files(payload, edge_bytes)

    assert [error.code for error in result.errors] == ["ENCODING_INVALID"]
    assert result.errors[0].file_kind == "node"
    assert result.errors[0].logical_record_no == 1
    assert result.nodes == ()


def test_utf16_bom_is_rejected_as_unknown_encoding() -> None:
    node_bytes, edge_bytes = _pair()
    payload = node_bytes.decode("utf-8").encode("utf-16")

    result = parse_import_files(payload, edge_bytes)

    assert [error.code for error in result.errors] == ["ENCODING_INVALID"]
    assert result.errors[0].file_kind == "node"


def test_empty_file_is_rejected() -> None:
    _, edge_bytes = _pair()

    result = parse_import_files(b"", edge_bytes)

    assert [error.code for error in result.errors] == ["FILE_EMPTY"]
    assert result.errors[0].logical_record_no == 1


def test_missing_header_reports_logical_record_no_one() -> None:
    payload = _csv_bytes(("node_id", "name"), [["N001", "Arithmetic_mean"]])

    result = parse_import_files(payload, _edge_bytes())

    node_errors = [error for error in result.errors if error.file_kind == "node"]
    assert [(error.code, error.logical_record_no) for error in node_errors] == [
        ("HEADER_MISSING", 1)
    ]
    assert result.nodes == ()


def test_bad_row_reports_its_logical_record_no() -> None:
    node_rows = [_node_row(), ["N002", "只给两列"], _second_node_row()]

    result = parse_import_files(_csv_bytes(NODE_COLUMNS, node_rows), _edge_bytes())

    node_errors = [error for error in result.errors if error.file_kind == "node"]
    assert [(error.code, error.logical_record_no) for error in node_errors] == [
        ("ROW_FIELD_COUNT", 2)
    ]
    assert [(node.source_key, node.logical_record_no) for node in result.nodes] == [
        ("N001", 1),
        ("N002", 3),
    ]


def test_missing_id_reports_its_logical_record_no() -> None:
    node_rows = [_node_row(), _second_node_row(node_id=""), _node_row(node_id="N003")]

    result = parse_import_files(_csv_bytes(NODE_COLUMNS, node_rows), _edge_bytes())

    node_errors = [error for error in result.errors if error.file_kind == "node"]
    assert [
        (error.code, error.logical_record_no, error.source_key)
        for error in node_errors
    ] == [("MISSING_ID", 2, None)]
    assert [(node.source_key, node.logical_record_no) for node in result.nodes] == [
        ("N001", 1),
        ("N003", 3),
    ]


def test_duplicate_ids_are_reported() -> None:
    node_rows = [_node_row(), _second_node_row(node_id="N001", name="Dup")]

    result = parse_import_files(_csv_bytes(NODE_COLUMNS, node_rows), _edge_bytes())

    node_errors = [error for error in result.errors if error.file_kind == "node"]
    assert [
        (error.code, error.logical_record_no, error.source_key)
        for error in node_errors
    ] == [("DUPLICATE_ID", 2, "N001")]
    assert [node.source_key for node in result.nodes] == ["N001"]


def test_duplicate_edge_ids_are_reported() -> None:
    edge_rows = [_edge_row(), _edge_row(edge_type="transitive", strength="0.6")]

    result = parse_import_files(_csv_bytes(NODE_COLUMNS, [
        _node_row(), _second_node_row(),
    ]), _edge_bytes(edge_rows))

    edge_errors = [error for error in result.errors if error.file_kind == "edge"]
    assert [
        (error.code, error.logical_record_no, error.source_key)
        for error in edge_errors
    ] == [("DUPLICATE_ID", 2, "E001")]
    assert [edge.source_key for edge in result.edges] == ["E001"]


def test_missing_endpoint_is_reported_and_edge_rejected() -> None:
    edge_rows = [_edge_row(target_id="N999", target_name="Missing")]

    result = parse_import_files(
        _csv_bytes(NODE_COLUMNS, [_node_row(), _second_node_row()]),
        _edge_bytes(edge_rows),
    )

    edge_errors = [error for error in result.errors if error.file_kind == "edge"]
    assert [
        (error.code, error.logical_record_no, error.source_key)
        for error in edge_errors
    ] == [("MISSING_ENDPOINT", 1, "E001")]
    assert result.edges == ()


def test_endpoint_name_mismatch_is_reported_as_warning() -> None:
    edge_rows = [_edge_row(source_name="ArithmeticMean")]

    result = parse_import_files(
        _csv_bytes(NODE_COLUMNS, [_node_row(), _second_node_row()]),
        _edge_bytes(edge_rows),
    )

    assert result.errors == ()
    assert [
        (warning.code, warning.logical_record_no, warning.source_key)
        for warning in result.warnings
    ] == [("ENDPOINT_NAME_MISMATCH", 1, "E001")]
    assert len(result.edges) == 1


def test_real_csv_structural_types_and_chapters() -> None:
    node_bytes, edge_bytes = _real_files()

    result = parse_import_files(node_bytes, edge_bytes)

    assert result.counts["direct_count"] == 112
    assert result.counts["transitive_count"] == 180
    assert result.counts["chapter_count"] == 15
    assert sorted({node.candidate["chapter_code"] for node in result.nodes}) == [
        f"M{index:02d}" for index in range(1, 16)
    ]
