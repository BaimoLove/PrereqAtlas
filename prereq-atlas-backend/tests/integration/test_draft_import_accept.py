"""T11：接收全量 STAGED 批次到草稿（HTTP + 真实 MySQL）。

覆盖 plan T11 的 7 项验收：全量映射计数与 PENDING 候选、transitive 只留来源记录、
来源命名空间可逐概念回溯、同批次重复接收 409、FAILED/未知批次与非可写草稿拒绝、
名称与编码冲突定位且零写入、状态切换前后注入故障均回滚。
"""

from __future__ import annotations

import csv
import hashlib
import io
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import sessionmaker

from app.infra import db
from app.infra.config import get_settings
from app.main import create_app
from app.modules.catalog import public as catalog_public
from app.modules.identity.service import create_admin
from app.modules.ingestion import public as ingestion_public
from support.db import BACKEND_ROOT

pytestmark = pytest.mark.integration

PROJECT_ROOT = BACKEND_ROOT.parent
NODE_PATH = PROJECT_ROOT / "datamining_kg_nodes.csv"
EDGE_PATH = PROJECT_ROOT / "datamining_kg_edges.csv"
ADMIN = {"username": "Accept_Admin", "password": "Accept-admin-pass-123"}
STUDENT = {"username": "Accept_Student", "password": "Accept-student-pass-123"}


def _register_request():
    from app.common.http_contracts import RegisterRequest

    return RegisterRequest(**ADMIN)


@pytest.fixture
def accept_client(
    migrated_database, monkeypatch
) -> Iterator[tuple[TestClient, Engine, dict[str, str]]]:
    _, engine = migrated_database
    monkeypatch.setenv("JWT_SECRET", "accept-tests-only-signing-key-32-bytes")
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


def _csv_rows(path: Path) -> list[dict[str, str]]:
    raw = path.read_text(encoding="utf-8-sig")
    lines = [line for line in raw.splitlines() if line.strip() and not line.startswith("#")]
    return list(csv.DictReader(io.StringIO("\n".join(lines))))


def _stage(client: TestClient, headers: dict[str, str]) -> str:
    files = {
        "node_file": ("nodes.csv", NODE_PATH.read_bytes()),
        "edge_file": ("edges.csv", EDGE_PATH.read_bytes()),
    }
    response = client.post("/api/v1/admin/imports", headers=headers, files=files)
    assert response.status_code == 201, response.text
    return str(response.json()["batch_id"])


def _create_draft(client: TestClient, headers: dict[str, str], label: str) -> str:
    response = client.post(
        "/api/v1/admin/graph/drafts", headers=headers, json={"version_label": label}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["draft_id"])


def _accept(
    client: TestClient,
    headers: dict[str, str],
    draft_id: str,
    *,
    batch_id: str,
    expected_revision: int = 0,
):
    return client.post(
        f"/api/v1/admin/graph/drafts/{draft_id}/imports",
        headers=headers,
        json={"batch_id": batch_id, "expected_revision": expected_revision},
    )


def _count(engine: Engine, statement: str, **params: object) -> int:
    with engine.connect() as connection:
        return int(connection.execute(text(statement), params).scalar_one())


def _rows(engine: Engine, statement: str, **params: object) -> list[tuple]:
    with engine.connect() as connection:
        return [tuple(row) for row in connection.execute(text(statement), params).all()]


def test_accept_full_batch_writes_chapters_concepts_and_pending_relations(accept_client):
    """T11-01/02/03：120 概念、15 章节、112 条 PENDING 候选，transitive 只留来源，来源可回溯。"""
    client, engine, headers = accept_client
    batch_id = _stage(client, headers)
    draft_id = _create_draft(client, headers, "v1.0-import")

    response = _accept(client, headers, draft_id, batch_id=batch_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["new_revision"] == 1

    node_sha256 = hashlib.sha256(NODE_PATH.read_bytes()).hexdigest()
    namespace = f"import/{node_sha256[:16]}"
    summary = body["mapping_summary"]
    assert summary["batch_id"] == batch_id
    assert summary["draft_id"] == draft_id
    assert summary["source_namespace"] == namespace
    assert summary["chapter_count"] == 15
    assert summary["concept_count"] == 120
    assert summary["relation_count"] == 112
    assert summary["transitive_skipped"] == 180
    assert summary["mapped_node_count"] == 120
    assert summary["conflicts"] == []

    detail = client.get(f"/api/v1/admin/graph/drafts/{draft_id}", headers=headers)
    assert detail.status_code == 200
    content = detail.json()
    assert content["draft_revision"] == 1
    assert content["validated_revision"] is None
    assert len(content["chapters"]) == 15
    assert len(content["concepts"]) == 120
    assert len(content["relations"]) == 112
    assert content["review_summary"] == {
        "approved_count": 0,
        "rejected_count": 0,
        "pending_count": 112,
    }

    node_rows = _csv_rows(NODE_PATH)
    edge_rows = _csv_rows(EDGE_PATH)
    direct_ids = {row["edge_id"] for row in edge_rows if row["edge_type"] == "direct"}

    assert [chapter["display_order"] for chapter in content["chapters"]] == list(range(1, 16))
    chapters_by_code = {chapter["chapter_code"]: chapter for chapter in content["chapters"]}
    for row in node_rows:
        chapter = chapters_by_code[row["module_id"]]
        assert chapter["name"] == row["module_name"]
        assert chapter["display_order"] == int(row["module_order"])

    expected_concepts = {row["node_id"]: row for row in node_rows}
    assert [concept["code"] for concept in content["concepts"]] == sorted(expected_concepts)
    for concept in content["concepts"]:
        source = expected_concepts[concept["code"]]
        assert concept["content"]["label_zh"] == source["label_zh"]
        assert concept["content"]["label_en"] == source["label_en"]
        assert concept["content"]["chapter_code"] == source["module_id"]
        assert concept["content"]["node_type"] == source["node_type"]
        assert concept["content"]["introduction"] is None
        assert concept["content"]["learning_focus"] is None

    concept_ids = {concept["code"]: concept["concept_id"] for concept in content["concepts"]}
    expected_pairs = {
        (concept_ids[row["source_id"]], concept_ids[row["target_id"]]) for row in edge_rows
        if row["edge_type"] == "direct"
    }
    assert {
        (relation["source_concept_id"], relation["target_concept_id"])
        for relation in content["relations"]
    } == expected_pairs
    for relation in content["relations"]:
        assert relation["necessity"] == "REQUIRED"
        assert relation["review_status"] == "PENDING"
        assert relation["structural_type"] == "direct"
        assert relation["rationale"] is None
        assert relation["reviewed_by"] is None
        assert relation["reviewed_at"] is None
        assert relation["source_edge_key"] in direct_ids
    assert len({relation["source_edge_key"] for relation in content["relations"]}) == 112

    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM graph_relation WHERE version_id = :draft",
            draft=draft_id,
        )
        == 112
    )
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM graph_relation WHERE version_id = :draft "
            "AND structural_type = 'transitive'",
            draft=draft_id,
        )
        == 0
    )
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM import_raw_edge WHERE batch_id = :batch",
            batch=batch_id,
        )
        == 292
    )
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM import_raw_edge WHERE batch_id = :batch "
            "AND structural_type = 'transitive'",
            batch=batch_id,
        )
        == 180
    )
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM import_raw_node WHERE batch_id = :batch "
            "AND mapping_status = 'MAPPED' AND mapped_concept_id IS NOT NULL",
            batch=batch_id,
        )
        == 120
    )
    traced = _rows(
        engine,
        "SELECT n.source_node_key, c.code, c.source_namespace, c.source_key, n.mapped_concept_id "
        "FROM import_raw_node n JOIN concept c ON c.id = n.mapped_concept_id "
        "WHERE n.batch_id = :batch",
        batch=batch_id,
    )
    assert len(traced) == 120
    for source_node_key, code, source_namespace, source_key, mapped_id in traced:
        assert source_node_key == source_key
        assert code == source_node_key
        assert source_namespace == namespace
        assert int(mapped_id) == int(concept_ids[code])

    status = _rows(engine, "SELECT status FROM import_batch WHERE id = :batch", batch=batch_id)
    assert status == [("CONSUMED",)]
    audit = _rows(
        engine,
        "SELECT action, revision_before, revision_after FROM graph_audit "
        "WHERE version_id = :draft ORDER BY id",
        draft=draft_id,
    )
    assert audit[-1] == ("IMPORT", 0, 1)


def test_second_accept_of_same_batch_is_conflict_without_new_rows(accept_client):
    """T11-04：同一批次重复接收返回 409，草稿内容、revision 与审计均不新增。"""
    client, engine, headers = accept_client
    batch_id = _stage(client, headers)
    draft_id = _create_draft(client, headers, "v1.0-dup")

    first = _accept(client, headers, draft_id, batch_id=batch_id)
    assert first.status_code == 200, first.text
    assert first.json()["new_revision"] == 1

    second = _accept(client, headers, draft_id, batch_id=batch_id, expected_revision=1)
    assert second.status_code == 409, second.text
    body = second.json()
    assert body["code"] == "CONFLICT"
    assert body["details"] == {
        "resource_type": "import_batch",
        "ids": [batch_id],
        "status": "CONSUMED",
    }

    detail = client.get(f"/api/v1/admin/graph/drafts/{draft_id}", headers=headers).json()
    assert detail["draft_revision"] == 1
    assert len(detail["chapters"]) == 15
    assert len(detail["concepts"]) == 120
    assert len(detail["relations"]) == 112
    assert _count(engine, "SELECT COUNT(*) FROM concept_revision") == 120
    assert _count(engine, "SELECT COUNT(*) FROM graph_relation") == 112
    assert _rows(
        engine,
        "SELECT action FROM graph_audit WHERE version_id = :draft ORDER BY id",
        draft=draft_id,
    ) == [("CREATE_DRAFT",), ("IMPORT",)]


def test_unacceptable_batch_draft_state_and_revision_are_rejected_without_writes(
    accept_client,
):
    """T11-05：FAILED/未知批次、未知/已发布/旧 revision 草稿全部拒绝，且一行不写。"""
    client, engine, headers = accept_client
    batch_id = _stage(client, headers)

    failed = client.post(
        "/api/v1/admin/imports",
        headers=headers,
        files={
            "node_file": ("nodes.csv", b""),
            "edge_file": ("edges.csv", EDGE_PATH.read_bytes()),
        },
    )
    assert failed.status_code == 422, failed.text
    failed_batch_id = str(failed.json()["details"]["batch_id"])

    draft_id = _create_draft(client, headers, "v1.0-reject")

    rejected_failed = _accept(client, headers, draft_id, batch_id=failed_batch_id)
    assert rejected_failed.status_code == 422, rejected_failed.text
    assert rejected_failed.json()["details"] == {
        "resource_type": "import_batch",
        "ids": [failed_batch_id],
        "status": "FAILED",
    }

    unknown_batch = _accept(client, headers, draft_id, batch_id="999999999")
    assert unknown_batch.status_code == 404
    assert unknown_batch.json()["code"] == "NOT_FOUND"

    unknown_draft = _accept(client, headers, "999999999", batch_id=batch_id)
    assert unknown_draft.status_code == 404

    stale = _accept(client, headers, draft_id, batch_id=batch_id, expected_revision=5)
    assert stale.status_code == 409
    assert stale.json()["details"] == {"expected_revision": 5, "actual_revision": 0}

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO graph_version (version_label, state, draft_revision, content_hash, "
                "created_by, published_by, published_at) "
                "VALUES ('v0.9-published', 'PUBLISHED', 0, :hash, 1, 1, '2026-10-27 09:00:00')"
            ),
            {"hash": "a" * 64},
        )
        published_id = str(
            connection.execute(
                text("SELECT id FROM graph_version WHERE version_label = 'v0.9-published'")
            ).scalar_one()
        )
    published = _accept(client, headers, published_id, batch_id=batch_id)
    assert published.status_code == 409
    assert published.json()["details"] == {
        "resource_type": "graph_version",
        "ids": [published_id],
        "state": "PUBLISHED",
    }

    registered = client.post("/api/v1/auth/register", json=STUDENT)
    assert registered.status_code == 201
    login = client.post("/api/v1/auth/token", data=STUDENT)
    student_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    forbidden = _accept(client, student_headers, draft_id, batch_id=batch_id, expected_revision=1)
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "FORBIDDEN"

    assert _count(engine, "SELECT COUNT(*) FROM chapter_revision") == 0
    assert _count(engine, "SELECT COUNT(*) FROM concept") == 0
    assert _count(engine, "SELECT COUNT(*) FROM concept_revision") == 0
    assert _count(engine, "SELECT COUNT(*) FROM graph_relation") == 0
    assert _rows(engine, "SELECT status FROM import_batch WHERE id = :batch", batch=batch_id) == [
        ("STAGED",)
    ]
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM import_raw_node WHERE batch_id = :batch "
            "AND mapping_status <> 'UNMAPPED'",
            batch=batch_id,
        )
        == 0
    )
    assert _rows(
        engine,
        "SELECT action, draft_revision FROM graph_audit a JOIN graph_version v "
        "ON v.id = a.version_id WHERE a.version_id = :draft",
        draft=draft_id,
    ) == [("CREATE_DRAFT", 0)]


def test_code_and_source_conflicts_are_located_without_overwriting(accept_client):
    """T11-06：编码被其他来源占用、来源身份已对应其他编码时，422 定位且零写入。"""
    client, engine, headers = accept_client
    namespace = f"import/{hashlib.sha256(NODE_PATH.read_bytes()).hexdigest()[:16]}"
    with engine.begin() as connection:
        code_taken_id = int(
            connection.execute(
                text("INSERT INTO concept (code) VALUES ('N001')")
            ).lastrowid
            or 0
        )
        source_taken_id = int(
            connection.execute(
                text(
                    "INSERT INTO concept (code, source_namespace, source_key) "
                    "VALUES ('ALT-N002', :namespace, 'N002')"
                ),
                {"namespace": namespace},
            ).lastrowid
            or 0
        )

    batch_id = _stage(client, headers)
    draft_id = _create_draft(client, headers, "v1.0-code-conflict")
    response = _accept(client, headers, draft_id, batch_id=batch_id)
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    conflicts = {
        (conflict["kind"], conflict["source_key"]): conflict
        for conflict in body["details"]["conflicts"]
    }
    assert set(conflicts) == {("CODE_TAKEN", "N001"), ("SOURCE_TAKEN", "N002")}
    assert conflicts[("CODE_TAKEN", "N001")]["existing_concept_id"] == str(code_taken_id)
    assert conflicts[("CODE_TAKEN", "N001")]["code"] == "N001"
    assert conflicts[("SOURCE_TAKEN", "N002")]["existing_concept_id"] == str(source_taken_id)
    assert conflicts[("SOURCE_TAKEN", "N002")]["code"] == "N002"
    assert all(conflict["message"] for conflict in conflicts.values())

    assert _count(engine, "SELECT COUNT(*) FROM concept") == 2
    assert _rows(engine, "SELECT code, source_namespace FROM concept ORDER BY id") == [
        ("N001", None),
        ("ALT-N002", namespace),
    ]
    assert _count(engine, "SELECT COUNT(*) FROM chapter_revision") == 0
    assert _count(engine, "SELECT COUNT(*) FROM concept_revision") == 0
    assert _count(engine, "SELECT COUNT(*) FROM graph_relation") == 0
    assert _rows(engine, "SELECT status FROM import_batch WHERE id = :batch", batch=batch_id) == [
        ("STAGED",)
    ]
    assert _rows(
        engine,
        "SELECT draft_revision FROM graph_version WHERE id = :draft",
        draft=draft_id,
    ) == [(0,)]


def _node_bytes_with_duplicate_label() -> tuple[bytes, str]:
    """把 N002 的中文名改成与 N001 相同，用于构造批次内名称冲突。"""
    raw = NODE_PATH.read_text(encoding="utf-8-sig")
    lines = [line for line in raw.splitlines() if line.strip() and not line.startswith("#")]
    rows = list(csv.reader(io.StringIO("\n".join(lines))))
    label_index = rows[0].index("label_zh")
    duplicate_label = rows[1][label_index]
    for row in rows[1:]:
        if row[0] == "N002":
            row[label_index] = duplicate_label
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerows(rows)
    return buffer.getvalue().encode("utf-8"), duplicate_label


def test_duplicate_label_inside_batch_is_conflict_and_writes_nothing(accept_client):
    """T11-06：同一批次两个节点中文名相同按冲突阻断，不生成重复名称的概念。"""
    client, engine, headers = accept_client
    node_bytes, duplicate_label = _node_bytes_with_duplicate_label()
    uploaded = client.post(
        "/api/v1/admin/imports",
        headers=headers,
        files={
            "node_file": ("nodes.csv", node_bytes),
            "edge_file": ("edges.csv", EDGE_PATH.read_bytes()),
        },
    )
    assert uploaded.status_code == 201, uploaded.text
    batch_id = str(uploaded.json()["batch_id"])
    draft_id = _create_draft(client, headers, "v1.0-label-conflict")

    response = _accept(client, headers, draft_id, batch_id=batch_id)
    assert response.status_code == 422, response.text
    conflicts = response.json()["details"]["conflicts"]
    assert {(item["kind"], item["source_key"]) for item in conflicts} == {
        ("DUPLICATE_LABEL", "N001"),
        ("DUPLICATE_LABEL", "N002"),
    }
    assert all(duplicate_label in item["message"] for item in conflicts)

    assert _count(engine, "SELECT COUNT(*) FROM concept") == 0
    assert _count(engine, "SELECT COUNT(*) FROM chapter_revision") == 0
    assert _count(engine, "SELECT COUNT(*) FROM concept_revision") == 0
    assert _count(engine, "SELECT COUNT(*) FROM graph_relation") == 0
    assert _rows(engine, "SELECT status FROM import_batch WHERE id = :batch", batch=batch_id) == [
        ("STAGED",)
    ]


def test_failure_before_and_after_status_switch_rolls_back_consistently(
    accept_client, monkeypatch
):
    """T11-07：内容写入中途、批次状态切换之后注入故障，草稿与批次都回到接收前。"""
    client, engine, headers = accept_client
    batch_id = _stage(client, headers)
    draft_id = _create_draft(client, headers, "v1.0-faulty")

    original_ensure = catalog_public.ensure_concept
    calls = {"count": 0}

    def flaky_ensure(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 10:
            raise RuntimeError("injected mapping failure")
        return original_ensure(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(catalog_public, "ensure_concept", flaky_ensure)
        failed_mapping = _accept(client, headers, draft_id, batch_id=batch_id)
    assert failed_mapping.status_code == 500, failed_mapping.text
    assert failed_mapping.json()["code"] == "INTERNAL_ERROR"
    assert _count(engine, "SELECT COUNT(*) FROM concept") == 0
    assert _count(engine, "SELECT COUNT(*) FROM chapter_revision") == 0
    assert _count(engine, "SELECT COUNT(*) FROM concept_revision") == 0
    assert _count(engine, "SELECT COUNT(*) FROM graph_relation") == 0
    assert _rows(engine, "SELECT status FROM import_batch WHERE id = :batch", batch=batch_id) == [
        ("STAGED",)
    ]
    assert _rows(
        engine, "SELECT draft_revision FROM graph_version WHERE id = :draft", draft=draft_id
    ) == [(0,)]
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM import_raw_node WHERE batch_id = :batch "
            "AND mapping_status <> 'UNMAPPED'",
            batch=batch_id,
        )
        == 0
    )

    retried = _accept(client, headers, draft_id, batch_id=batch_id)
    assert retried.status_code == 200, retried.text
    assert retried.json()["new_revision"] == 1

    second_batch = _stage(client, headers)
    second_draft = _create_draft(client, headers, "v1.1-faulty")

    def explode(*args, **kwargs):
        raise RuntimeError("injected status switch failure")

    with monkeypatch.context() as patch:
        patch.setattr(ingestion_public, "mark_consumed", explode)
        failed_switch = _accept(client, headers, second_draft, batch_id=second_batch)
    assert failed_switch.status_code == 500, failed_switch.text
    assert _rows(
        engine, "SELECT status FROM import_batch WHERE id = :batch", batch=second_batch
    ) == [("STAGED",)]
    assert _rows(
        engine,
        "SELECT draft_revision FROM graph_version WHERE id = :draft",
        draft=second_draft,
    ) == [(0,)]
    assert _count(engine, "SELECT COUNT(*) FROM concept") == 120
    assert _count(engine, "SELECT COUNT(*) FROM concept_revision") == 120
    assert _count(engine, "SELECT COUNT(*) FROM graph_relation") == 112
    assert _rows(
        engine,
        "SELECT action FROM graph_audit WHERE version_id = :draft ORDER BY id",
        draft=second_draft,
    ) == [("CREATE_DRAFT",)]

    third = _accept(client, headers, second_draft, batch_id=second_batch, expected_revision=0)
    assert third.status_code == 200, third.text
    assert third.json()["new_revision"] == 1
    assert _rows(
        engine, "SELECT status FROM import_batch WHERE id = :batch", batch=second_batch
    ) == [("CONSUMED",)]
