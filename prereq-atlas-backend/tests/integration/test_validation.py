"""T14：草稿完整校验与报告（HTTP + 真实 MySQL）。

验收映射：
- T14-01：全部候选都审核通过且图合法时返回 valid 报告；
- T14-02：未审核或拒绝的必需关系阻断发布；
- T14-05：通过时保存当前 revision，失败不写入、不改 revision、不留校验记录。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import sessionmaker

from app.infra import db
from app.infra.config import get_settings
from app.main import create_app
from app.modules.graph_admin import repository as graph_repository
from app.modules.graph_admin.models import GraphRelation
from app.modules.identity.service import create_admin
from support.db import BACKEND_ROOT

pytestmark = pytest.mark.integration

PROJECT_ROOT = BACKEND_ROOT.parent
NODE_PATH = PROJECT_ROOT / "datamining_kg_nodes.csv"
EDGE_PATH = PROJECT_ROOT / "datamining_kg_edges.csv"
ADMIN = {"username": "Validate_Admin", "password": "Validate-admin-pass-123"}


def _register_request():
    from app.common.http_contracts import RegisterRequest

    return RegisterRequest(**ADMIN)


@pytest.fixture
def validation_client(
    migrated_database, monkeypatch
) -> Iterator[tuple[TestClient, Engine, dict[str, str]]]:
    _, engine = migrated_database
    monkeypatch.setenv("JWT_SECRET", "validation-tests-only-signing-key-32")
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


def _stage(client: TestClient, headers: dict[str, str]) -> str:
    files = {
        "node_file": ("nodes.csv", NODE_PATH.read_bytes()),
        "edge_file": ("edges.csv", EDGE_PATH.read_bytes()),
    }
    response = client.post("/api/v1/admin/imports", headers=headers, files=files)
    assert response.status_code == 201, response.text
    return str(response.json()["batch_id"])


def _draft_with_candidates(
    client: TestClient, headers: dict[str, str], label: str = "v1.0-validation"
) -> tuple[str, dict]:
    """上传真实全量数据并接收进空草稿，得到 112 条 PENDING 候选。"""
    batch_id = _stage(client, headers)
    created = client.post(
        "/api/v1/admin/graph/drafts", headers=headers, json={"version_label": label}
    )
    assert created.status_code == 201, created.text
    draft_id = str(created.json()["draft_id"])
    accepted = client.post(
        f"/api/v1/admin/graph/drafts/{draft_id}/imports",
        headers=headers,
        json={"batch_id": batch_id, "expected_revision": 0},
    )
    assert accepted.status_code == 200, accepted.text
    detail = client.get(f"/api/v1/admin/graph/drafts/{draft_id}", headers=headers)
    assert detail.status_code == 200
    return draft_id, detail.json()


def _validate(
    client: TestClient, headers: dict[str, str], draft_id: str, expected_revision: int
):
    return client.post(
        f"/api/v1/admin/graph/drafts/{draft_id}/validate",
        headers=headers,
        json={"expected_revision": expected_revision},
    )


def _draft_row(engine: Engine, draft_id: str) -> tuple[int, int | None]:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT draft_revision, validated_revision FROM graph_version "
                "WHERE id = :id"
            ),
            {"id": int(draft_id)},
        ).one()
        return int(row[0]), (int(row[1]) if row[1] is not None else None)


def _count(engine: Engine, statement: str, **params: object) -> int:
    with engine.connect() as connection:
        return int(connection.execute(text(statement), params).scalar_one())


def test_validation_blocks_unreviewed_candidates_then_records_the_clean_revision(
    validation_client,
) -> None:
    client, engine, headers = validation_client
    draft_id, detail = _draft_with_candidates(client, headers)
    relation_ids = [relation["id"] for relation in detail["relations"]]
    assert len(relation_ids) == 112
    assert _draft_row(engine, draft_id) == (1, None)

    blocked = _validate(client, headers, draft_id, expected_revision=1)

    assert blocked.status_code == 422, blocked.text
    body = blocked.json()
    assert body["code"] == "VALIDATION_ERROR"
    report = body["details"]["report"]
    assert report["draft_id"] == draft_id
    assert report["revision"] == 1
    assert report["valid"] is False
    assert [issue["code"] for issue in report["issues"]] == ["PENDING_RELATIONS"]
    assert report["issues"][0]["relation_ids"] == relation_ids
    assert (
        report["approved_count"],
        report["rejected_count"],
        report["pending_count"],
    ) == (0, 0, 112)
    assert report["cycle_paths"] == []
    assert _draft_row(engine, draft_id) == (1, None)

    reviewed = client.post(
        f"/api/v1/admin/graph/drafts/{draft_id}/relations/review",
        headers=headers,
        json={
            "relation_ids": relation_ids,
            "review_status": "APPROVED",
            "rationale": "全量候选一次性审核",
            "expected_revision": 1,
        },
    )
    assert reviewed.status_code == 200, reviewed.text

    passed = _validate(client, headers, draft_id, expected_revision=2)

    assert passed.status_code == 200, passed.text
    assert passed.json() == {
        "draft_id": draft_id,
        "revision": 2,
        "valid": True,
        "issues": [],
        "approved_count": 112,
        "rejected_count": 0,
        "pending_count": 0,
        "cycle_paths": [],
    }
    assert _draft_row(engine, draft_id) == (2, 2)
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM graph_audit WHERE version_id = :id "
            "AND action = 'VALIDATE_DRAFT'",
            id=int(draft_id),
        )
        == 1
    )
    refreshed = client.get(f"/api/v1/admin/graph/drafts/{draft_id}", headers=headers)
    assert refreshed.status_code == 200
    assert refreshed.json()["validation_summary"] == {
        "validated_revision": 2,
        "valid": True,
        "issue_count": 0,
    }


def test_validation_reports_the_cycle_and_leaves_the_previous_revision(
    validation_client,
) -> None:
    client, engine, headers = validation_client
    draft_id, detail = _draft_with_candidates(client, headers)
    relation_ids = [relation["id"] for relation in detail["relations"]]
    reviewed = client.post(
        f"/api/v1/admin/graph/drafts/{draft_id}/relations/review",
        headers=headers,
        json={
            "relation_ids": relation_ids,
            "review_status": "APPROVED",
            "rationale": "先形成合法 DAG",
            "expected_revision": 1,
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    first = detail["relations"][0]
    source_id, target_id = first["source_concept_id"], first["target_concept_id"]

    created = client.post(
        f"/api/v1/admin/graph/drafts/{draft_id}/relations",
        headers=headers,
        json={
            "source_concept_id": target_id,
            "target_concept_id": source_id,
            "necessity": "REQUIRED",
            "rationale": "故意制造回边",
            "expected_revision": 2,
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["new_revision"] == 3
    back_edge_id = created.json()["relation"]["id"]
    approved_back_edge = client.post(
        f"/api/v1/admin/graph/drafts/{draft_id}/relations/review",
        headers=headers,
        json={
            "relation_ids": [back_edge_id],
            "review_status": "APPROVED",
            "rationale": "把回边也审核通过，暴露环",
            "expected_revision": 3,
        },
    )
    assert approved_back_edge.status_code == 200, approved_back_edge.text

    blocked = _validate(client, headers, draft_id, expected_revision=4)

    assert blocked.status_code == 422, blocked.text
    body = blocked.json()
    assert body["code"] == "VALIDATION_ERROR"
    report = body["details"]["report"]
    assert report["valid"] is False
    assert [issue["code"] for issue in report["issues"]] == ["CYCLE"]
    path = report["cycle_paths"][0]
    assert len(path) == 3
    assert path[0] == path[-1]
    assert set(path) == {source_id, target_id}
    assert set(report["issues"][0]["concept_ids"]) == {source_id, target_id}
    assert report["issues"][0]["relation_ids"] == []
    assert report["approved_count"] == 113
    assert _draft_row(engine, draft_id) == (4, None)


def test_validation_names_pending_and_rejected_relations_separately(
    validation_client,
) -> None:
    client, engine, headers = validation_client
    draft_id, detail = _draft_with_candidates(client, headers)
    relation_ids = [relation["id"] for relation in detail["relations"]]
    approved_ids = relation_ids[:-2]
    rejected_id, pending_id = relation_ids[-2], relation_ids[-1]

    reviewed = client.post(
        f"/api/v1/admin/graph/drafts/{draft_id}/relations/review",
        headers=headers,
        json={
            "relation_ids": approved_ids,
            "review_status": "APPROVED",
            "rationale": "批量通过",
            "expected_revision": 1,
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    rejected = client.post(
        f"/api/v1/admin/graph/drafts/{draft_id}/relations/review",
        headers=headers,
        json={
            "relation_ids": [rejected_id],
            "review_status": "REJECTED",
            "rationale": "证据不足",
            "expected_revision": 2,
        },
    )
    assert rejected.status_code == 200, rejected.text

    blocked = _validate(client, headers, draft_id, expected_revision=3)

    assert blocked.status_code == 422, blocked.text
    report = blocked.json()["details"]["report"]
    assert [issue["code"] for issue in report["issues"]] == [
        "PENDING_RELATIONS",
        "REJECTED_RELATIONS",
    ]
    assert report["issues"][0]["relation_ids"] == [pending_id]
    assert report["issues"][1]["relation_ids"] == [rejected_id]
    assert (
        report["approved_count"],
        report["rejected_count"],
        report["pending_count"],
    ) == (110, 1, 1)
    assert report["cycle_paths"] == []
    assert _draft_row(engine, draft_id) == (3, None)


def test_validation_rejects_a_stale_revision_without_writing(validation_client) -> None:
    client, engine, headers = validation_client
    draft_id, _ = _draft_with_candidates(client, headers)

    blocked = _validate(client, headers, draft_id, expected_revision=0)

    assert blocked.status_code == 409, blocked.text
    body = blocked.json()
    assert body["code"] == "CONFLICT"
    assert body["details"]["expected_revision"] == 0
    assert body["details"]["actual_revision"] == 1
    assert _draft_row(engine, draft_id) == (1, None)
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM graph_audit WHERE version_id = :id "
            "AND action = 'VALIDATE_DRAFT'",
            id=int(draft_id),
        )
        == 0
    )


def test_validation_is_admin_only_and_unknown_drafts_are_not_found(
    validation_client,
) -> None:
    client, engine, headers = validation_client
    draft_id, _ = _draft_with_candidates(client, headers)
    registered = client.post(
        "/api/v1/auth/register",
        json={"username": "Validate_Student", "password": "student-pass-123"},
    )
    assert registered.status_code == 201, registered.text
    login = client.post(
        "/api/v1/auth/token",
        data={"username": "Validate_Student", "password": "student-pass-123"},
    )
    assert login.status_code == 200, login.text
    student_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    forbidden = _validate(client, student_headers, draft_id, expected_revision=1)
    assert forbidden.status_code == 403, forbidden.text
    assert forbidden.json()["code"] == "FORBIDDEN"

    missing = _validate(client, headers, "999999", expected_revision=1)
    assert missing.status_code == 404, missing.text
    assert missing.json()["code"] == "NOT_FOUND"
    assert _draft_row(engine, draft_id) == (1, None)


def test_validation_locates_stored_data_problems_one_by_one(
    validation_client, monkeypatch
) -> None:
    client, engine, headers = validation_client
    draft_id, detail = _draft_with_candidates(client, headers)
    relation_ids = [relation["id"] for relation in detail["relations"]]
    reviewed = client.post(
        f"/api/v1/admin/graph/drafts/{draft_id}/relations/review",
        headers=headers,
        json={
            "relation_ids": relation_ids,
            "review_status": "APPROVED",
            "rationale": "先得到合法草图",
            "expected_revision": 1,
        },
    )
    assert reviewed.status_code == 200, reviewed.text

    first = detail["relations"][0]
    source_id = int(first["source_concept_id"])
    target_id = int(first["target_concept_id"])

    def _synthetic(relation_id: int, source: int, target: int) -> GraphRelation:
        return GraphRelation(
            id=relation_id,
            version_id=int(draft_id),
            source_concept_id=source,
            target_concept_id=target,
            necessity="REQUIRED",
            review_status="APPROVED",
            structural_type="direct",
        )

    planted = [
        _synthetic(9900001, source_id, source_id),
        _synthetic(9900003, source_id, 999999),
        _synthetic(9900002, source_id, target_id),
    ]
    original = graph_repository.list_relations

    def _with_planted(session, version_id):
        return [*original(session, version_id), *planted]

    monkeypatch.setattr(graph_repository, "list_relations", _with_planted)

    blocked = _validate(client, headers, draft_id, expected_revision=2)

    assert blocked.status_code == 422, blocked.text
    report = blocked.json()["details"]["report"]
    assert [issue["code"] for issue in report["issues"]] == [
        "SELF_LOOP",
        "UNKNOWN_ENDPOINT",
        "DUPLICATE_EDGE",
    ]
    assert report["issues"][0]["concept_ids"] == [str(source_id)]
    assert report["issues"][0]["relation_ids"] == ["9900001"]
    assert report["issues"][1]["concept_ids"] == ["999999"]
    assert report["issues"][1]["relation_ids"] == ["9900003"]
    assert report["issues"][2]["relation_ids"] == [first["id"], "9900002"]
    assert _draft_row(engine, draft_id) == (2, None)


def test_validation_stamp_lapses_as_soon_as_the_draft_changes(validation_client) -> None:
    client, engine, headers = validation_client
    draft_id, detail = _draft_with_candidates(client, headers)
    relation_ids = [relation["id"] for relation in detail["relations"]]
    reviewed = client.post(
        f"/api/v1/admin/graph/drafts/{draft_id}/relations/review",
        headers=headers,
        json={
            "relation_ids": relation_ids,
            "review_status": "APPROVED",
            "rationale": "先通过校验",
            "expected_revision": 1,
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    passed = _validate(client, headers, draft_id, expected_revision=2)
    assert passed.status_code == 200, passed.text
    assert _draft_row(engine, draft_id) == (2, 2)

    removed = client.post(
        f"/api/v1/admin/graph/drafts/{draft_id}/relations/{relation_ids[0]}/delete",
        headers=headers,
        json={"expected_revision": 2},
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["new_revision"] == 3

    refreshed = client.get(f"/api/v1/admin/graph/drafts/{draft_id}", headers=headers)
    assert refreshed.status_code == 200
    assert refreshed.json()["validation_summary"] == {
        "validated_revision": None,
        "valid": False,
        "issue_count": 0,
    }
    assert _draft_row(engine, draft_id) == (3, None)

    again = _validate(client, headers, draft_id, expected_revision=3)
    assert again.status_code == 200, again.text
    assert again.json()["revision"] == 3
    assert again.json()["approved_count"] == 111
    assert _draft_row(engine, draft_id) == (3, 3)
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM graph_audit WHERE version_id = :id "
            "AND action = 'VALIDATE_DRAFT'",
            id=int(draft_id),
        )
        == 2
    )
