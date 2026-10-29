"""T12：关系新增/更新/审核/删除（HTTP + 真实 MySQL）。

覆盖 plan T12 的 8 项验收：112 条候选一次批量审核只递增一次 revision、审核记录完整、
理由与批量边界校验、混入未知或他草稿 ID 整批失败、旧 revision、改端点退回 PENDING、
RECOMMENDED/OPTIONAL 拒绝、重复/自环/无效端点与审计准确性。
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
from app.modules.identity.service import create_admin
from support.db import BACKEND_ROOT

pytestmark = pytest.mark.integration

PROJECT_ROOT = BACKEND_ROOT.parent
NODE_PATH = PROJECT_ROOT / "datamining_kg_nodes.csv"
EDGE_PATH = PROJECT_ROOT / "datamining_kg_edges.csv"
ADMIN = {"username": "Relation_Admin", "password": "Relation-admin-pass-123"}
STUDENT = {"username": "Relation_Student", "password": "Relation-student-pass-123"}


def _register_request():
    from app.common.http_contracts import RegisterRequest

    return RegisterRequest(**ADMIN)


@pytest.fixture
def relations_client(
    migrated_database, monkeypatch
) -> Iterator[tuple[TestClient, Engine, dict[str, str]]]:
    _, engine = migrated_database
    monkeypatch.setenv("JWT_SECRET", "relation-tests-only-signing-key-32-byte")
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
    client: TestClient, headers: dict[str, str], label: str = "v1.0-relations"
) -> tuple[str, dict]:
    """上传真实全量数据并把批次接收进一个空草稿，得到 112 条 PENDING 候选。"""
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


def _create_relation(
    client: TestClient,
    headers: dict[str, str],
    draft_id: str,
    *,
    source: str,
    target: str,
    expected_revision: int,
    **extra: object,
):
    payload: dict[str, object] = {
        "source_concept_id": source,
        "target_concept_id": target,
        "expected_revision": expected_revision,
    }
    payload.update(extra)
    return client.post(
        f"/api/v1/admin/graph/drafts/{draft_id}/relations", headers=headers, json=payload
    )


def _free_pair(detail: dict) -> tuple[str, str]:
    """选一对当前草稿内尚无关系的概念，用于新增。"""
    existing = {
        (relation["source_concept_id"], relation["target_concept_id"])
        for relation in detail["relations"]
    }
    concepts = [concept["concept_id"] for concept in detail["concepts"]]
    for source in concepts:
        for target in concepts:
            if source != target and (source, target) not in existing:
                return source, target
    raise AssertionError("测试夹具里找不到没有关系的概念对")


def _count(engine: Engine, statement: str, **params: object) -> int:
    with engine.connect() as connection:
        return int(connection.execute(text(statement), params).scalar_one())


def _rows(engine: Engine, statement: str, **params: object) -> list[tuple]:
    with engine.connect() as connection:
        return [tuple(row) for row in connection.execute(text(statement), params).all()]


def test_relation_can_be_created_read_back_and_deleted(relations_client):
    """T12-08：合法新增返回 201 与 PENDING 关系，删除返回新 revision，学生 403。"""
    client, engine, headers = relations_client
    draft_id, detail = _draft_with_candidates(client, headers)
    source, target = _free_pair(detail)
    revisions_before = detail["draft_revision"]

    created = _create_relation(
        client,
        headers,
        draft_id,
        source=source,
        target=target,
        expected_revision=revisions_before,
        rationale="课程组确认先修方向成立",
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["new_revision"] == revisions_before + 1
    relation = body["relation"]
    assert (relation["source_concept_id"], relation["target_concept_id"]) == (source, target)
    assert relation["necessity"] == "REQUIRED"
    assert relation["review_status"] == "PENDING"
    assert relation["rationale"] == "课程组确认先修方向成立"
    assert relation["reviewed_by"] is None
    assert relation["reviewed_at"] is None
    assert relation["structural_type"] is None
    relation_id = relation["id"]

    after_create = client.get(f"/api/v1/admin/graph/drafts/{draft_id}", headers=headers).json()
    assert after_create["draft_revision"] == revisions_before + 1
    assert len(after_create["relations"]) == 113
    assert after_create["review_summary"]["pending_count"] == 113
    actions = _rows(
        engine,
        "SELECT action, revision_before, revision_after FROM graph_audit "
        "WHERE version_id = :draft ORDER BY id",
        draft=draft_id,
    )
    assert actions[-1] == ("EDIT_RELATION", revisions_before, revisions_before + 1)

    deleted = client.post(
        f"/api/v1/admin/graph/drafts/{draft_id}/relations/{relation_id}/delete",
        headers=headers,
        json={"expected_revision": revisions_before + 1},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"new_revision": revisions_before + 2}
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM graph_relation WHERE id = :id",
            id=relation_id,
        )
        == 0
    )
    after_delete = client.get(f"/api/v1/admin/graph/drafts/{draft_id}", headers=headers).json()
    assert len(after_delete["relations"]) == 112

    missing = client.post(
        f"/api/v1/admin/graph/drafts/{draft_id}/relations/999999999/delete",
        headers=headers,
        json={"expected_revision": revisions_before + 2},
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == "NOT_FOUND"

    registered = client.post("/api/v1/auth/register", json=STUDENT)
    assert registered.status_code == 201
    login = client.post("/api/v1/auth/token", data=STUDENT)
    student_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    forbidden = _create_relation(
        client,
        student_headers,
        draft_id,
        source=source,
        target=target,
        expected_revision=revisions_before + 2,
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "FORBIDDEN"
    assert _count(engine, "SELECT COUNT(*) FROM graph_relation") == 112


def _review(
    client: TestClient,
    headers: dict[str, str],
    draft_id: str,
    *,
    relation_ids: list[str],
    expected_revision: int,
    review_status: str = "APPROVED",
    rationale: str = "逐条核对先修方向，均成立",
):
    return client.post(
        f"/api/v1/admin/graph/drafts/{draft_id}/relations/review",
        headers=headers,
        json={
            "relation_ids": relation_ids,
            "review_status": review_status,
            "rationale": rationale,
            "expected_revision": expected_revision,
        },
    )


def test_batch_review_of_all_candidates_uses_single_revision(relations_client):
    """T12-01/02：112 条候选一次提交，affected_count=112、revision 只加 1、审核记录完整。"""
    client, engine, headers = relations_client
    draft_id, detail = _draft_with_candidates(client, headers)
    relation_ids = [relation["id"] for relation in detail["relations"]]
    assert len(relation_ids) == 112
    assert detail["review_summary"]["pending_count"] == 112
    admin_id = _rows(engine, "SELECT id FROM app_user WHERE role = 'ADMIN'")[0][0]

    response = _review(
        client, headers, draft_id, relation_ids=relation_ids, expected_revision=1
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["affected_count"] == 112
    assert body["new_revision"] == 2

    rows = _rows(
        engine,
        "SELECT review_status, rationale, reviewed_by, reviewed_at, structural_type "
        "FROM graph_relation WHERE version_id = :draft",
        draft=draft_id,
    )
    assert len(rows) == 112
    for review_status, rationale, reviewed_by, reviewed_at, structural_type in rows:
        assert review_status == "APPROVED"
        assert rationale == "逐条核对先修方向，均成立"
        assert int(reviewed_by) == int(admin_id)
        assert reviewed_at is not None
        assert structural_type == "direct"

    after = client.get(f"/api/v1/admin/graph/drafts/{draft_id}", headers=headers).json()
    assert after["draft_revision"] == 2
    assert after["review_summary"] == {
        "approved_count": 112,
        "rejected_count": 0,
        "pending_count": 0,
    }
    assert all(
        relation["reviewed_by"] == str(admin_id) and relation["reviewed_at"] is not None
        for relation in after["relations"]
    )

    actions = _rows(
        engine,
        "SELECT action, revision_before, revision_after FROM graph_audit "
        "WHERE version_id = :draft ORDER BY id",
        draft=draft_id,
    )
    assert actions == [("CREATE_DRAFT", None, 0), ("IMPORT", 0, 1), ("EDIT_RELATION", 1, 2)]


def test_batch_review_rejects_empty_rationale_and_out_of_range_id_lists(relations_client):
    """T12-03：空理由、空批次与超过 112 条都在模型层被拒，且不改变任何关系。"""
    client, engine, headers = relations_client
    draft_id, detail = _draft_with_candidates(client, headers)
    relation_ids = [relation["id"] for relation in detail["relations"]]

    empty_rationale = _review(
        client, headers, draft_id, relation_ids=relation_ids[:2], expected_revision=1,
        rationale="   ",
    )
    assert empty_rationale.status_code == 422, empty_rationale.text
    assert empty_rationale.json()["code"] == "VALIDATION_ERROR"

    empty_batch = _review(
        client, headers, draft_id, relation_ids=[], expected_revision=1
    )
    assert empty_batch.status_code == 422

    too_many = _review(
        client,
        headers,
        draft_id,
        relation_ids=[str(index) for index in range(1, 114)],
        expected_revision=1,
    )
    assert too_many.status_code == 422

    too_long_rationale = _review(
        client,
        headers,
        draft_id,
        relation_ids=relation_ids[:2],
        expected_revision=1,
        rationale="x" * 501,
    )
    assert too_long_rationale.status_code == 422

    assert _rows(
        engine,
        "SELECT review_status, rationale, reviewed_by FROM graph_relation "
        "WHERE version_id = :draft",
        draft=draft_id,
    ) == [("PENDING", None, None)] * 112
    assert _rows(
        engine, "SELECT draft_revision FROM graph_version WHERE id = :draft", draft=draft_id
    ) == [(1,)]
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM graph_audit WHERE version_id = :draft "
            "AND action = 'EDIT_RELATION'",
            draft=draft_id,
        )
        == 0
    )


def test_batch_review_rejects_foreign_and_unknown_ids_and_stale_revision(relations_client):
    """T12-04/05：混入他草稿或未知 ID 整批失败且零修改；旧 revision 409 且零修改。"""
    client, engine, headers = relations_client
    draft_id, detail = _draft_with_candidates(client, headers, "v1.0-first")
    other_draft_id, other_detail = _draft_with_candidates(client, headers, "v2.0-second")
    own_relation_id = detail["relations"][0]["id"]
    other_relation_id = other_detail["relations"][0]["id"]

    mixed = _review(
        client,
        headers,
        draft_id,
        relation_ids=[own_relation_id, other_relation_id, "999999999"],
        expected_revision=1,
    )
    assert mixed.status_code == 422, mixed.text
    body = mixed.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["details"] == {
        "resource_type": "graph_relation",
        "ids": [other_relation_id, "999999999"],
    }
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM graph_relation WHERE review_status <> 'PENDING'",
        )
        == 0
    )
    assert _rows(
        engine, "SELECT draft_revision FROM graph_version WHERE id = :draft", draft=draft_id
    ) == [(1,)]
    assert _rows(
        engine,
        "SELECT draft_revision FROM graph_version WHERE id = :draft",
        draft=other_draft_id,
    ) == [(1,)]

    stale = _review(
        client, headers, draft_id, relation_ids=[own_relation_id], expected_revision=0
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["details"] == {"expected_revision": 0, "actual_revision": 1}
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM graph_relation WHERE review_status <> 'PENDING'",
        )
        == 0
    )


def _update(
    client: TestClient,
    headers: dict[str, str],
    draft_id: str,
    relation_id: str,
    *,
    expected_revision: int,
    **fields: object,
):
    payload: dict[str, object] = {"expected_revision": expected_revision}
    payload.update(fields)
    return client.post(
        f"/api/v1/admin/graph/drafts/{draft_id}/relations/{relation_id}/update",
        headers=headers,
        json=payload,
    )


def test_single_review_then_endpoint_change_resets_to_pending(relations_client):
    """T12-06：单条审核后改端点回到 PENDING，并让旧校验结论失效。"""
    client, engine, headers = relations_client
    draft_id, detail = _draft_with_candidates(client, headers)
    relation = detail["relations"][0]
    relation_id = relation["id"]
    admin_id = _rows(engine, "SELECT id FROM app_user WHERE role = 'ADMIN'")[0][0]

    approved = _update(
        client,
        headers,
        draft_id,
        relation_id,
        expected_revision=1,
        review_status="APPROVED",
        rationale="课程组确认该直接先修成立",
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["new_revision"] == 2
    assert body["relation"]["review_status"] == "APPROVED"
    assert body["relation"]["rationale"] == "课程组确认该直接先修成立"
    assert body["relation"]["reviewed_by"] == str(admin_id)
    assert body["relation"]["reviewed_at"] is not None
    assert (body["relation"]["source_concept_id"], body["relation"]["target_concept_id"]) == (
        relation["source_concept_id"],
        relation["target_concept_id"],
    )

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE graph_version SET validated_revision = 2 WHERE id = :draft"),
            {"draft": draft_id},
        )

    taken = {
        (item["source_concept_id"], item["target_concept_id"])
        for item in detail["relations"]
    }
    new_target = next(
        concept["concept_id"]
        for concept in detail["concepts"]
        if concept["concept_id"] != relation["source_concept_id"]
        and (relation["source_concept_id"], concept["concept_id"]) not in taken
    )
    changed = _update(
        client,
        headers,
        draft_id,
        relation_id,
        expected_revision=2,
        target_concept_id=new_target,
    )
    assert changed.status_code == 200, changed.text
    changed_body = changed.json()
    assert changed_body["new_revision"] == 3
    assert changed_body["relation"]["target_concept_id"] == new_target
    assert changed_body["relation"]["review_status"] == "PENDING"
    assert changed_body["relation"]["reviewed_by"] is None
    assert changed_body["relation"]["reviewed_at"] is None
    assert changed_body["relation"]["rationale"] is None

    after = client.get(f"/api/v1/admin/graph/drafts/{draft_id}", headers=headers).json()
    assert after["validated_revision"] is None
    assert after["draft_revision"] == 3
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM graph_version WHERE id = :draft "
            "AND validated_revision IS NOT NULL",
            draft=draft_id,
        )
        == 0
    )
    actions = _rows(
        engine,
        "SELECT revision_before, revision_after, summary FROM graph_audit "
        "WHERE version_id = :draft AND action = 'EDIT_RELATION' ORDER BY id",
        draft=draft_id,
    )
    assert [(before, after) for before, after, _ in actions] == [(1, 2), (2, 3)]
    assert all(summary for _, _, summary in actions)


def test_necessity_and_review_field_combinations_are_rejected(relations_client):
    """T12-07：RECOMMENDED/OPTIONAL 与非法审核组合一律 422，且不产生修改。"""
    client, engine, headers = relations_client
    draft_id, detail = _draft_with_candidates(client, headers)
    source, target = _free_pair(detail)
    relation_id = detail["relations"][0]["id"]

    for necessity in ("RECOMMENDED", "OPTIONAL"):
        created = _create_relation(
            client,
            headers,
            draft_id,
            source=source,
            target=target,
            expected_revision=1,
            necessity=necessity,
        )
        assert created.status_code == 422, created.text
        updated = _update(
            client,
            headers,
            draft_id,
            relation_id,
            expected_revision=1,
            necessity=necessity,
        )
        assert updated.status_code == 422, updated.text

    pending = _update(
        client,
        headers,
        draft_id,
        relation_id,
        expected_revision=1,
        review_status="PENDING",
        rationale="想改回未审核",
    )
    assert pending.status_code == 422

    no_rationale = _update(
        client, headers, draft_id, relation_id, expected_revision=1, review_status="APPROVED"
    )
    assert no_rationale.status_code == 422

    mixed = _update(
        client,
        headers,
        draft_id,
        relation_id,
        expected_revision=1,
        target_concept_id=detail["concepts"][1]["concept_id"],
        review_status="APPROVED",
        rationale="同一次混改",
    )
    assert mixed.status_code == 422

    empty_change = _update(
        client, headers, draft_id, relation_id, expected_revision=1
    )
    assert empty_change.status_code == 422

    assert _rows(
        engine,
        "SELECT review_status, necessity FROM graph_relation WHERE version_id = :draft",
        draft=draft_id,
    ) == [("PENDING", "REQUIRED")] * 112
    assert _rows(
        engine, "SELECT draft_revision FROM graph_version WHERE id = :draft", draft=draft_id
    ) == [(1,)]


def test_duplicate_self_loop_and_unknown_endpoint_are_rejected(relations_client):
    """T12-08：同版重复、自环与无效端点被拒，审计保持准确且 revision 不变。"""
    client, engine, headers = relations_client
    draft_id, detail = _draft_with_candidates(client, headers)
    relation = detail["relations"][0]
    existing_pair = (relation["source_concept_id"], relation["target_concept_id"])

    duplicate = _create_relation(
        client,
        headers,
        draft_id,
        source=existing_pair[0],
        target=existing_pair[1],
        expected_revision=1,
    )
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["code"] == "CONFLICT"
    assert duplicate.json()["details"] == {
        "resource_type": "graph_relation",
        "ids": [relation["id"]],
    }

    self_loop = _create_relation(
        client,
        headers,
        draft_id,
        source=existing_pair[0],
        target=existing_pair[0],
        expected_revision=1,
    )
    assert self_loop.status_code == 422, self_loop.text

    unknown_endpoint = _create_relation(
        client,
        headers,
        draft_id,
        source=existing_pair[0],
        target="999999999",
        expected_revision=1,
    )
    assert unknown_endpoint.status_code == 422, unknown_endpoint.text
    assert unknown_endpoint.json()["details"] == {
        "resource_type": "concept",
        "ids": ["999999999"],
    }

    assert _count(engine, "SELECT COUNT(*) FROM graph_relation") == 112
    assert _rows(
        engine, "SELECT draft_revision FROM graph_version WHERE id = :draft", draft=draft_id
    ) == [(1,)]
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM graph_audit WHERE version_id = :draft "
            "AND action = 'EDIT_RELATION'",
            draft=draft_id,
        )
        == 0
    )
