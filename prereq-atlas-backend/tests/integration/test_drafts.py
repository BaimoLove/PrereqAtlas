"""T10：草稿创建、复制、读取与版本列表（HTTP + 真实 MySQL）。

覆盖 plan T10 的 5 项验收：空草稿初值、发布版复制与源版不变、标签/来源拒绝无半成品、
详情与列表过滤分页、复制故障回滚且审计无假成功。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import sessionmaker

from app.common.contracts import ErrorCode
from app.infra import db
from app.infra.config import get_settings
from app.infra.errors import AppError
from app.main import create_app
from app.modules.catalog import public as catalog_public
from app.modules.graph_admin.public import ensure_draft_writable
from app.modules.identity.service import create_admin

pytestmark = pytest.mark.integration

ADMIN = {"username": "Draft_Admin", "password": "Draft-admin-pass-123"}
STUDENT = {"username": "Draft_Student", "password": "Draft-student-pass-123"}
PUBLISHED_AT = datetime(2026, 10, 27, 10, 0, 0)
SOURCE_HASH = "e" * 64


@pytest.fixture
def drafts_client(
    migrated_database, monkeypatch
) -> Iterator[tuple[TestClient, Engine, dict[str, str]]]:
    _, engine = migrated_database
    monkeypatch.setenv("JWT_SECRET", "draft-tests-only-signing-key-32-bytes")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60")
    get_settings.cache_clear()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(db, "get_session_factory", lambda: factory)
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        with factory() as session:
            create_admin(session, _register_request(ADMIN))
            session.commit()
        login = client.post("/api/v1/auth/token", data=ADMIN)
        assert login.status_code == 200
        token = login.json()["access_token"]
        yield client, engine, {"Authorization": f"Bearer {token}"}
    get_settings.cache_clear()


def _register_request(credentials: dict[str, str]):
    from app.common.http_contracts import RegisterRequest

    return RegisterRequest(**credentials)


def _count(engine: Engine, statement: str, **params: object) -> int:
    with engine.connect() as connection:
        return int(connection.execute(text(statement), params).scalar_one())


def _scalar_row(engine: Engine, statement: str, **params: object):
    with engine.connect() as connection:
        return connection.execute(text(statement), params).one()


def _seed_published(engine: Engine, *, label: str = "v1.0") -> tuple[int, int, int]:
    """插入一个已发布版本：2 章、2 概念、2 资料、1 条 APPROVED/REQUIRED 关系。"""
    with engine.begin() as connection:
        alpha = int(
            connection.execute(
                text(
                    "INSERT INTO concept (code, source_namespace, source_key) "
                    "VALUES ('APRIORI', 'AL-CPL/data_mining', 'N010')"
                )
            ).lastrowid
            or 0
        )
        beta = int(
            connection.execute(
                text(
                    "INSERT INTO concept (code, source_namespace, source_key) "
                    "VALUES ('KNN', 'AL-CPL/data_mining', 'N011')"
                )
            ).lastrowid
            or 0
        )
        version_id = int(
            connection.execute(
                text(
                    "INSERT INTO graph_version (version_label, state, draft_revision, "
                    "content_hash, created_by, published_by, published_at) "
                    "VALUES (:label, 'PUBLISHED', 0, :hash, 1, 1, :published_at)"
                ),
                {"label": label, "hash": SOURCE_HASH, "published_at": PUBLISHED_AT},
            ).lastrowid
            or 0
        )
        connection.execute(
            text(
                "INSERT INTO chapter_revision (version_id, chapter_code, name, display_order) "
                "VALUES (:version_id, 'M01', '数据挖掘概述', 1), (:version_id, 'M02', '关联规则', 2)"
            ),
            {"version_id": version_id},
        )
        connection.execute(
            text(
                "INSERT INTO concept_revision (version_id, concept_id, chapter_code, label_zh, "
                "label_en, node_type, introduction, learning_focus) VALUES "
                "(:version_id, :alpha, 'M01', 'Apriori 算法', 'Apriori', '算法', '频繁项集挖掘', NULL), "
                "(:version_id, :beta, 'M02', 'K 近邻', NULL, '方法', NULL, '距离度量')"
            ),
            {"version_id": version_id, "alpha": alpha, "beta": beta},
        )
        connection.execute(
            text(
                "INSERT INTO resource_revision (version_id, concept_id, title, url, source) VALUES "
                "(:version_id, :alpha, '教材第 6 章', 'https://example.com/apriori', '课程教材'), "
                "(:version_id, :alpha, '讲义 3', 'http://example.com/slides.pdf', NULL)"
            ),
            {"version_id": version_id, "alpha": alpha},
        )
        connection.execute(
            text(
                "INSERT INTO graph_relation (version_id, source_concept_id, target_concept_id, "
                "necessity, structural_type, review_status, rationale, source_edge_key, "
                "reviewed_by, reviewed_at) VALUES (:version_id, :alpha, :beta, 'REQUIRED', "
                "'direct', 'APPROVED', '先修基础算法再学习分类器', 'E001', 1, :reviewed_at)"
            ),
            {"version_id": version_id, "alpha": alpha, "beta": beta, "reviewed_at": PUBLISHED_AT},
        )
    return version_id, alpha, beta


def _create_draft(client: TestClient, headers: dict[str, str], **payload: object):
    return client.post("/api/v1/admin/graph/drafts", headers=headers, json=payload)


def test_create_empty_draft_starts_at_zero_revision(drafts_client):
    """T10-01：空草稿为 DRAFT、revision 初值 0，并留下一条创建审计。"""
    client, engine, headers = drafts_client
    response = _create_draft(client, headers, version_label="  v0.1  ")
    assert response.status_code == 201
    body = response.json()
    assert body["revision"] == 0
    draft_id = int(body["draft_id"])

    detail = client.get(f"/api/v1/admin/graph/drafts/{body['draft_id']}", headers=headers)
    assert detail.status_code == 200
    content = detail.json()
    assert content["draft_id"] == body["draft_id"]
    assert content["version_label"] == "v0.1"
    assert content["state"] == "DRAFT"
    assert content["base_version_id"] is None
    assert content["draft_revision"] == 0
    assert content["validated_revision"] is None
    assert content["chapters"] == []
    assert content["concepts"] == []
    assert content["relations"] == []
    assert content["resources"] == []
    assert content["review_summary"] == {
        "approved_count": 0,
        "rejected_count": 0,
        "pending_count": 0,
    }
    assert content["validation_summary"] == {
        "validated_revision": None,
        "valid": False,
        "issue_count": 0,
    }

    state, revision = _scalar_row(
        engine, "SELECT state, draft_revision FROM graph_version WHERE id = :id", id=draft_id
    )
    assert (state, revision) == ("DRAFT", 0)
    action, before, after = _scalar_row(
        engine,
        "SELECT action, revision_before, revision_after FROM graph_audit WHERE version_id = :id",
        id=draft_id,
    )
    assert (action, before, after) == ("CREATE_DRAFT", None, 0)

    invalid_payloads = (
        {"version_label": ""},
        {"version_label": "x" * 65},
        {"version_label": "v9", "owner_id": "1"},
    )
    for payload in invalid_payloads:
        rejected = _create_draft(client, headers, **payload)
        assert rejected.status_code == 422, payload
        assert rejected.json()["code"] == "VALIDATION_ERROR"
    assert _count(engine, "SELECT COUNT(*) FROM graph_version") == 1


def test_create_draft_from_published_copies_content_and_relations(drafts_client):
    """T10-02：复制发布版得到完整内容与关系，源版本一行未改、哈希不变。"""
    client, engine, headers = drafts_client
    source_id, alpha_id, beta_id = _seed_published(engine)

    created = _create_draft(
        client, headers, version_label="v1.1", base_version_id=str(source_id)
    )
    assert created.status_code == 201
    body = created.json()
    assert body["revision"] == 1
    draft_id = int(body["draft_id"])

    detail = client.get(f"/api/v1/admin/graph/drafts/{body['draft_id']}", headers=headers)
    assert detail.status_code == 200
    content = detail.json()
    assert content["base_version_id"] == str(source_id)
    assert content["draft_revision"] == 1
    assert [(c["chapter_code"], c["display_order"]) for c in content["chapters"]] == [
        ("M01", 1),
        ("M02", 2),
    ]
    assert [c["code"] for c in content["concepts"]] == ["APRIORI", "KNN"]
    assert [c["concept_id"] for c in content["concepts"]] == [str(alpha_id), str(beta_id)]
    assert content["concepts"][0]["content"]["label_zh"] == "Apriori 算法"
    assert content["concepts"][0]["content"]["introduction"] == "频繁项集挖掘"
    assert [
        (r["concept_id"], r["title"], r["url"], r["source"]) for r in content["resources"]
    ] == [
        (str(alpha_id), "教材第 6 章", "https://example.com/apriori", "课程教材"),
        (str(alpha_id), "讲义 3", "http://example.com/slides.pdf", None),
    ]
    assert len(content["relations"]) == 1
    relation = content["relations"][0]
    assert (relation["source_concept_id"], relation["target_concept_id"]) == (
        str(alpha_id),
        str(beta_id),
    )
    assert relation["necessity"] == "REQUIRED"
    assert relation["review_status"] == "APPROVED"
    assert relation["rationale"] == "先修基础算法再学习分类器"
    assert relation["reviewed_by"] == "1"
    assert relation["structural_type"] == "direct"
    assert relation["source_edge_key"] == "E001"
    assert relation["reviewed_at"] is not None
    assert content["review_summary"] == {
        "approved_count": 1,
        "rejected_count": 0,
        "pending_count": 0,
    }

    state, content_hash, revision, published_by = _scalar_row(
        engine,
        "SELECT state, content_hash, draft_revision, published_by "
        "FROM graph_version WHERE id = :id",
        id=source_id,
    )
    assert (state, content_hash, revision, published_by) == ("PUBLISHED", SOURCE_HASH, 0, 1)
    for table in ("chapter_revision", "concept_revision", "resource_revision", "graph_relation"):
        assert (
            _count(engine, f"SELECT COUNT(*) FROM {table} WHERE version_id = :id", id=source_id)
            > 0
        )
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM resource_revision WHERE version_id = :id",
            id=source_id,
        )
        == 2
    )

    action, before, after = _scalar_row(
        engine,
        "SELECT action, revision_before, revision_after FROM graph_audit WHERE version_id = :id",
        id=draft_id,
    )
    assert (action, before, after) == ("CREATE_DRAFT", None, 1)


def test_duplicate_label_and_invalid_base_are_rejected_without_rows(drafts_client):
    """T10-03：重名标签 409，未知或非发布来源 422，均不留半成品。"""
    client, engine, headers = drafts_client
    source_id, _, _ = _seed_published(engine)

    duplicate = _create_draft(client, headers, version_label="v1.0")
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "CONFLICT"

    unknown = _create_draft(
        client, headers, version_label="v1.2", base_version_id="999999999"
    )
    assert unknown.status_code == 422
    assert unknown.json()["details"] == {
        "resource_type": "graph_version",
        "ids": ["999999999"],
    }

    draft = _create_draft(client, headers, version_label="v1.3")
    assert draft.status_code == 201
    draft_id = draft.json()["draft_id"]

    not_published = _create_draft(client, headers, version_label="v1.4", base_version_id=draft_id)
    assert not_published.status_code == 422
    assert not_published.json()["details"] == {
        "resource_type": "graph_version",
        "ids": [draft_id],
        "state": "DRAFT",
    }

    assert _count(engine, "SELECT COUNT(*) FROM graph_version") == 2
    assert _count(engine, "SELECT COUNT(*) FROM graph_audit") == 1
    assert _count(engine, "SELECT COUNT(*) FROM chapter_revision") == 2
    assert _count(engine, "SELECT COUNT(*) FROM concept_revision") == 2
    assert _count(engine, "SELECT COUNT(*) FROM resource_revision") == 2
    assert _count(engine, "SELECT COUNT(*) FROM graph_relation") == 1
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM graph_version WHERE id = :id AND content_hash = :hash",
            id=source_id,
            hash=SOURCE_HASH,
        )
        == 1
    )


def test_version_list_filters_orders_and_draft_detail_not_found(drafts_client):
    """T10-04：版本列表按创建时间/ID 倒序稳定排列，状态过滤与生效标记正确。"""
    client, engine, headers = drafts_client
    source_id, _, _ = _seed_published(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO active_graph (singleton_id, version_id, revision, updated_by) "
                "VALUES (1, :version_id, 3, 1)"
            ),
            {"version_id": source_id},
        )

    second = _create_draft(client, headers, version_label="v2.0")
    assert second.status_code == 201
    third = _create_draft(client, headers, version_label="v3.0")
    assert third.status_code == 201
    second_id, third_id = int(second.json()["draft_id"]), int(third.json()["draft_id"])

    listing = client.get("/api/v1/admin/graph/versions", headers=headers)
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert listing.json()["total"] == 3
    assert [int(item["version_id"]) for item in items] == [third_id, second_id, source_id]
    assert [item["version_label"] for item in items] == ["v3.0", "v2.0", "v1.0"]

    drafts = client.get("/api/v1/admin/graph/versions?state=DRAFT", headers=headers)
    assert drafts.status_code == 200
    assert drafts.json()["total"] == 2
    assert [int(item["version_id"]) for item in drafts.json()["items"]] == [third_id, second_id]
    assert all(item["content_hash"] is None for item in drafts.json()["items"])
    assert all(item["is_active"] is False for item in drafts.json()["items"])
    assert all(item["active_revision"] is None for item in drafts.json()["items"])

    published = client.get("/api/v1/admin/graph/versions?state=PUBLISHED", headers=headers)
    assert published.status_code == 200
    published_item = published.json()["items"][0]
    assert int(published_item["version_id"]) == source_id
    assert published_item["content_hash"] == SOURCE_HASH
    assert published_item["is_active"] is True
    assert published_item["active_revision"] == 3
    assert published_item["published_at"].endswith("+08:00")

    paged = client.get("/api/v1/admin/graph/versions?page=2&page_size=2", headers=headers)
    assert paged.status_code == 200
    assert len(paged.json()["items"]) == 1
    assert int(paged.json()["items"][0]["version_id"]) == source_id
    empty = client.get("/api/v1/admin/graph/versions?page=3&page_size=2", headers=headers)
    assert empty.status_code == 200
    assert empty.json()["items"] == []

    assert client.get(
        "/api/v1/admin/graph/versions?state=ARCHIVED", headers=headers
    ).status_code == 422
    missing = client.get("/api/v1/admin/graph/drafts/999999999", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["code"] == "NOT_FOUND"


def test_only_admin_may_read_or_create_drafts(drafts_client):
    """T10-04：学生读取列表与创建草稿均 403，不产生任何写入。"""
    client, engine, headers = drafts_client
    registered = client.post("/api/v1/auth/register", json=STUDENT)
    assert registered.status_code == 201
    login = client.post("/api/v1/auth/token", data=STUDENT)
    student_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    assert client.get("/api/v1/admin/graph/versions", headers=student_headers).status_code == 403
    forbidden = _create_draft(client, student_headers, version_label="v-student")
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "FORBIDDEN"
    assert _count(engine, "SELECT COUNT(*) FROM graph_version") == 0
    assert _count(engine, "SELECT COUNT(*) FROM graph_audit") == 0


def test_copy_failure_rolls_back_without_false_audit(drafts_client, monkeypatch):
    """T10-05：复制中途故障整体回滚，草稿与审计均不出现假成功。"""
    client, engine, headers = drafts_client
    source_id, _, _ = _seed_published(engine)
    original = catalog_public.copy_content
    calls = {"count": 0}

    def flaky(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("injected copy failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(catalog_public, "copy_content", flaky)
    failed = _create_draft(
        client, headers, version_label="v1.1", base_version_id=str(source_id)
    )
    assert failed.status_code == 500
    assert failed.json()["code"] == "INTERNAL_ERROR"
    assert _count(engine, "SELECT COUNT(*) FROM graph_version") == 1
    assert _count(engine, "SELECT COUNT(*) FROM graph_audit") == 0
    assert _count(engine, "SELECT COUNT(*) FROM chapter_revision") == 2
    assert _count(engine, "SELECT COUNT(*) FROM concept_revision") == 2
    assert _count(engine, "SELECT COUNT(*) FROM graph_relation") == 1

    retried = _create_draft(
        client, headers, version_label="v1.1", base_version_id=str(source_id)
    )
    assert retried.status_code == 201
    assert retried.json()["revision"] == 1
    assert _count(engine, "SELECT COUNT(*) FROM graph_version") == 2
    assert _count(engine, "SELECT COUNT(*) FROM graph_audit") == 1


def test_production_gate_rejects_published_and_stale_revision(drafts_client):
    """补充：草稿写入口的生产 gate 只放行 DRAFT 且 revision 匹配的版本。"""
    client, engine, headers = drafts_client
    source_id, _, _ = _seed_published(engine)
    created = _create_draft(client, headers, version_label="v2.0")
    assert created.status_code == 201
    draft_id = int(created.json()["draft_id"])

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        with pytest.raises(AppError) as published:
            ensure_draft_writable(session, version_id=source_id, expected_revision=0)
        session.rollback()
        with pytest.raises(AppError) as stale:
            ensure_draft_writable(session, version_id=draft_id, expected_revision=5)
        session.rollback()
        assert ensure_draft_writable(session, version_id=draft_id, expected_revision=0) is None
        session.rollback()

    assert published.value.code is ErrorCode.CONFLICT
    assert published.value.details == {
        "resource_type": "graph_version",
        "ids": [str(source_id)],
        "state": "PUBLISHED",
    }
    assert stale.value.code is ErrorCode.CONFLICT
    assert stale.value.details == {"expected_revision": 5, "actual_revision": 0}
    assert _count(engine, "SELECT COUNT(*) FROM chapter_revision WHERE version_id = :id", id=draft_id) == 0
