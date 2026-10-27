"""T08：通过 HTTP 与真实 MySQL 验证导入暂存、报告、回滚和权限。"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import sessionmaker

from app.infra import db
from app.infra.config import get_settings
from app.main import create_app
from app.modules.identity.service import create_admin
from app.modules.ingestion import repository
from support.db import BACKEND_ROOT

pytestmark = pytest.mark.integration

PROJECT_ROOT = BACKEND_ROOT.parent
NODE_PATH = PROJECT_ROOT / "datamining_kg_nodes.csv"
EDGE_PATH = PROJECT_ROOT / "datamining_kg_edges.csv"
ADMIN = {"username": "Import_Admin", "password": "Import-admin-pass-123"}


@pytest.fixture
def import_client(
    migrated_database, monkeypatch
) -> Iterator[tuple[TestClient, Engine, dict[str, str]]]:
    _, engine = migrated_database
    monkeypatch.setenv("JWT_SECRET", "import-tests-only-signing-key-32-bytes")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60")
    get_settings.cache_clear()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(db, "get_session_factory", lambda: factory)
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        with factory() as session:
            create_admin(session, _register_request())
            session.commit()
        login = client.post("/api/v1/auth/token", data=ADMIN)
        assert login.status_code == 200
        token = login.json()["access_token"]
        yield client, engine, {"Authorization": f"Bearer {token}"}
    get_settings.cache_clear()


def _register_request():
    from app.common.http_contracts import RegisterRequest

    return RegisterRequest(**ADMIN)


def _csv_rows(path: Path) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8-sig")
    lines = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
    return list(csv.DictReader(io.StringIO("\n".join(lines))))


def _upload(
    client: TestClient,
    headers: dict[str, str],
    *,
    node_bytes: bytes | None = None,
    edge_bytes: bytes | None = None,
    note: str | None = None,
):
    node = node_bytes if node_bytes is not None else NODE_PATH.read_bytes()
    edge = edge_bytes if edge_bytes is not None else EDGE_PATH.read_bytes()
    files = {
        "node_file": ("nodes.csv", node),
        "edge_file": ("edges.csv", edge),
    }
    data = {} if note is None else {"source_note": note}
    return client.post("/api/v1/admin/imports", headers=headers, files=files, data=data)


def _count(engine: Engine, table: str) -> int:
    with engine.connect() as connection:
        return int(connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())


def test_full_upload_stages_all_raw_records(import_client):
    client, engine, headers = import_client
    response = _upload(client, headers, note="2026-10-27 基线导入")
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "STAGED"
    assert body["counts"]["node_count"] == 120
    assert body["counts"]["edge_count"] == 292
    assert body["counts"]["direct_count"] == 112
    assert body["counts"]["transitive_count"] == 180
    assert body["counts"]["chapter_count"] == 15
    assert body["source_note"] == "2026-10-27 基线导入"
    assert body["node_sha256"] == hashlib.sha256(NODE_PATH.read_bytes()).hexdigest()
    assert body["edge_sha256"] == hashlib.sha256(EDGE_PATH.read_bytes()).hexdigest()

    with engine.connect() as connection:
        status, note = connection.execute(
            text("SELECT status, source_note FROM import_batch WHERE id=:id"),
            {"id": int(body["batch_id"])},
        ).one()
    assert status == body["status"]
    assert note == body["source_note"]
    assert _count(engine, "import_raw_node") == 120
    assert _count(engine, "import_raw_edge") == 292


def test_every_raw_record_preserves_source_values_and_line_numbers(import_client):
    client, engine, headers = import_client
    created = _upload(client, headers)
    assert created.status_code == 201
    batch_id = int(created.json()["batch_id"])

    expected_nodes = _csv_rows(NODE_PATH)
    expected_edges = _csv_rows(EDGE_PATH)
    with engine.connect() as connection:
        nodes = connection.execute(
            text(
                "SELECT source_node_key, logical_record_no, raw_json, mapping_status "
                "FROM import_raw_node WHERE batch_id=:batch ORDER BY logical_record_no"
            ),
            {"batch": batch_id},
        ).all()
        edges = connection.execute(
            text(
                "SELECT source_edge_key, logical_record_no, source_node_key, "
                "target_node_key, structural_type, raw_json "
                "FROM import_raw_edge WHERE batch_id=:batch ORDER BY logical_record_no"
            ),
            {"batch": batch_id},
        ).all()

    assert [row.logical_record_no for row in nodes] == list(range(1, 121))
    assert [row.logical_record_no for row in edges] == list(range(1, 293))
    for row, expected in zip(nodes, expected_nodes, strict=True):
        assert row.source_node_key == expected["node_id"]
        assert row.mapping_status == "UNMAPPED"
        assert json.loads(row.raw_json) == expected
        assert json.loads(row.raw_json)["source"] == expected["source"]
    for row, expected in zip(edges, expected_edges, strict=True):
        assert row.source_edge_key == expected["edge_id"]
        assert row.source_node_key == expected["source_id"]
        assert row.target_node_key == expected["target_id"]
        assert row.structural_type == expected["edge_type"]
        assert json.loads(row.raw_json) == expected


def test_invalid_file_returns_422_with_queryable_failed_report(import_client):
    client, engine, headers = import_client
    response = _upload(client, headers, node_bytes=b"")
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    batch_id = body["details"]["batch_id"]
    assert int(batch_id) > 0
    assert body["details"]["errors"][0]["code"] == "FILE_EMPTY"

    report = client.get(f"/api/v1/admin/imports/{batch_id}", headers=headers)
    assert report.status_code == 200
    report_body = report.json()
    assert report_body["status"] == "FAILED"
    assert report_body["counts"]["node_count"] == 0
    assert report_body["errors"][0]["code"] == "FILE_EMPTY"
    assert _count(engine, "import_raw_node") == 0
    assert _count(engine, "import_raw_edge") == 0
    assert PROJECT_ROOT.as_posix() not in report.text


def test_persistence_failure_rolls_back_batch_and_rows(import_client, monkeypatch):
    client, engine, headers = import_client
    original = repository.add_raw_edges
    calls = {"count": 0}

    def flaky(session, batch_id, edges):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("injected persistence failure")
        return original(session, batch_id, edges)

    monkeypatch.setattr(repository, "add_raw_edges", flaky)
    failed = _upload(client, headers)
    assert failed.status_code == 500
    assert failed.json()["code"] == "INTERNAL_ERROR"
    assert _count(engine, "import_batch") == 0
    assert _count(engine, "import_raw_node") == 0
    assert _count(engine, "import_raw_edge") == 0

    retried = _upload(client, headers)
    assert retried.status_code == 201
    assert retried.json()["status"] == "STAGED"
    assert _count(engine, "import_batch") == 1
    assert _count(engine, "import_raw_node") == 120
    assert _count(engine, "import_raw_edge") == 292


def test_duplicate_upload_creates_new_batch_without_overwriting(import_client):
    client, engine, headers = import_client
    first = _upload(client, headers)
    assert first.status_code == 201
    first_id = first.json()["batch_id"]

    second = _upload(client, headers)
    assert second.status_code == 201
    second_body = second.json()
    assert second_body["batch_id"] != first_id
    assert second_body["status"] == "STAGED"
    assert second_body["mapping_summary"]["duplicate_of"] == first_id
    assert any(item["code"] == "DUPLICATE_BATCH" for item in second_body["warnings"])

    original = client.get(f"/api/v1/admin/imports/{first_id}", headers=headers)
    assert original.status_code == 200
    assert original.json()["counts"]["node_count"] == 120
    assert original.json()["counts"]["edge_count"] == 292
    assert original.json()["warnings"] == []
    assert _count(engine, "import_batch") == 2
    assert _count(engine, "import_raw_node") == 240
    assert _count(engine, "import_raw_edge") == 584


def test_non_admin_forbidden_unknown_batch_not_found_and_no_paths(import_client):
    client, _, headers = import_client
    registered = client.post(
        "/api/v1/auth/register",
        json={"username": "Import_Student", "password": "Import-student-pass-123"},
    )
    assert registered.status_code == 201
    login = client.post(
        "/api/v1/auth/token",
        data={"username": "Import_Student", "password": "Import-student-pass-123"},
    )
    student_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    forbidden = _upload(client, student_headers)
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "FORBIDDEN"

    missing = client.get("/api/v1/admin/imports/999999999", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["code"] == "NOT_FOUND"
    for response in (forbidden, missing):
        assert str(PROJECT_ROOT) not in response.text
        assert "nodes.csv" not in response.text
        assert "edges.csv" not in response.text


def test_detail_pagination_is_stable_across_nodes_and_edges(import_client):
    client, _, headers = import_client
    created = _upload(client, headers)
    assert created.status_code == 201
    batch_id = created.json()["batch_id"]

    second = client.get(
        f"/api/v1/admin/imports/{batch_id}?page=2&page_size=100", headers=headers
    )
    assert second.status_code == 200
    body = second.json()
    assert body["total"] == 412
    assert body["page"] == 2 and body["page_size"] == 100
    assert len(body["items"]) == 100
    assert body["items"][0]["file_kind"] == "node"
    assert body["items"][0]["logical_record_no"] == 101
    assert body["items"][19]["logical_record_no"] == 120
    assert body["items"][20]["file_kind"] == "edge"
    assert body["items"][20]["logical_record_no"] == 1

    last = client.get(
        f"/api/v1/admin/imports/{batch_id}?page=5&page_size=100", headers=headers
    )
    assert last.status_code == 200
    assert len(last.json()["items"]) == 12
    assert last.json()["items"][0]["logical_record_no"] == 281


def test_oversized_upload_is_rejected_without_batch(import_client):
    client, engine, headers = import_client
    oversized = b"x" * (10 * 1024 * 1024 + 1)
    response = _upload(client, headers, node_bytes=oversized)
    assert response.status_code == 413
    assert response.json()["code"] == "PAYLOAD_TOO_LARGE"
    assert _count(engine, "import_batch") == 0
