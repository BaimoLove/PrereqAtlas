"""T15: atomic publication through public boundaries and real MySQL."""

import pytest

from support.publication import prepared_draft, publish

pytestmark = pytest.mark.integration


def test_first_publication_freezes_the_full_graph_and_marks_one_active(publication_client):
    client, _, headers = publication_client
    draft = prepared_draft(client, headers)
    response = publish(client, headers, draft)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "version_id": draft["draft_id"],
        "published": True,
        "runtime_ready": True,
        "active_revision": 1,
    }
    versions = client.get("/api/v1/admin/graph/versions", headers=headers).json()["items"]
    assert len(versions) == 1
    assert versions[0]["state"] == "PUBLISHED"
    assert versions[0]["is_active"] is True
    assert len(versions[0]["content_hash"]) == 64
    assert versions[0]["published_at"] is not None
    detail = client.get(f"/api/v1/admin/graph/drafts/{draft['draft_id']}", headers=headers).json()
    assert len(detail["concepts"]) == 120
    assert len(detail["relations"]) == 112
    assert all(r["reviewed_by"] and r["reviewed_at"] for r in detail["relations"])


def test_pinned_context_survives_a_new_publication_and_rebuilds_after_restart(publication_client):
    from sqlalchemy.orm import Session

    from app.modules.graph_admin.public import pin_active_version, read_published_version
    from app.modules.graph_runtime.public import SnapshotCache

    client, engine, headers = publication_client
    first = prepared_draft(client, headers, "first")
    assert publish(client, headers, first).status_code == 200
    with Session(engine) as session:
        old = pin_active_version(session, cache=SnapshotCache())
    assert old.version_id == int(first["draft_id"])
    assert len(old.snapshot.node_index) == 120
    assert len(old.snapshot.edges) == 112
    assert old.data.content_hash == old.content_hash == old.snapshot.content_hash
    second = prepared_draft(client, headers, "second")
    assert publish(client, headers, second, active=1).status_code == 200
    with Session(engine) as session:
        current = pin_active_version(session, cache=SnapshotCache())
        historical = read_published_version(session, old.version_id, cache=SnapshotCache())
    assert current.version_id == int(second["draft_id"])
    assert current.active_revision == 2
    assert historical.data == old.data
    assert old.snapshot.version_id == old.data.version_id == old.version_id
    assert old.active_revision == 1


def test_two_first_publishers_have_one_success_and_one_conflict(publication_client):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy import event

    client, engine, headers = publication_client
    first = prepared_draft(client, headers, "race-first")
    second = prepared_draft(client, headers, "race-second")
    rendezvous = Barrier(2, timeout=8)

    def synchronize_initial_reads(conn, cursor, statement, parameters, context, many):
        if "FROM active_graph" in statement and "FOR UPDATE" in statement:
            rendezvous.wait()

    event.listen(engine, "after_cursor_execute", synchronize_initial_reads)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            a = workers.submit(publish, client, headers, first)
            b = workers.submit(publish, client, headers, second)
            responses = [a.result(timeout=20), b.result(timeout=20)]
    finally:
        event.remove(engine, "after_cursor_execute", synchronize_initial_reads)
    assert sorted(r.status_code for r in responses) == [200, 409]
    loser = next(r for r in responses if r.status_code == 409)
    assert loser.json()["code"] == "CONFLICT"
    versions = client.get("/api/v1/admin/graph/versions", headers=headers).json()["items"]
    assert sum(v["is_active"] for v in versions) == 1
    assert sorted(v["state"] for v in versions) == ["DRAFT", "PUBLISHED"]
    assert next(v for v in versions if v["is_active"])["active_revision"] == 1


def test_readiness_requires_a_rebuildable_graph_but_bootstrap_remains_available(publication_client):
    client, _, headers = publication_client
    response = client.get("/health/ready")
    assert response.status_code == 503, response.text
    assert response.json()["code"] == "SERVICE_UNAVAILABLE"
    assert client.get("/health/live").status_code == 200
    login = client.post(
        "/api/v1/auth/token",
        data={
            "username": "Publish_Admin",
            "password": "Publish-pass-123",
        },
    )
    assert login.status_code == 200
    draft = prepared_draft(client, headers)
    assert publish(client, headers, draft).status_code == 200
    ready = client.get("/health/ready")
    assert ready.status_code == 200, ready.text
    assert ready.json()["database"] == "ok"


def test_stale_revisions_and_every_existing_edit_reject_published_content(publication_client):
    client, _, headers = publication_client
    first = prepared_draft(client, headers, "frozen")
    stale = {**first, "revision": first["revision"] - 1}
    assert publish(client, headers, stale).status_code == 409
    assert publish(client, headers, first, active=9).status_code == 409
    assert publish(client, headers, first).status_code == 200
    version_id = first["draft_id"]
    before = client.get(f"/api/v1/admin/graph/drafts/{version_id}", headers=headers).json()
    relation = before["relations"][0]
    root = f"/api/v1/admin/graph/drafts/{version_id}"
    mutations = [
        ("validate", {"expected_revision": 2}),
        ("publish", {"expected_revision": 2, "expected_active_revision": 1}),
        ("imports", {"expected_revision": 2, "batch_id": "999999"}),
        (
            "relations",
            {
                "expected_revision": 2,
                "source_concept_id": relation["source_concept_id"],
                "target_concept_id": relation["target_concept_id"],
                "necessity": "REQUIRED",
                "rationale": "immutable",
            },
        ),
        (
            "relations/review",
            {
                "expected_revision": 2,
                "relation_ids": [relation["id"]],
                "review_status": "REJECTED",
                "rationale": "immutable",
            },
        ),
        (f"relations/{relation['id']}/update", {"expected_revision": 2, "rationale": "immutable"}),
        (f"relations/{relation['id']}/delete", {"expected_revision": 2}),
    ]
    for path, body in mutations:
        response = client.post(f"{root}/{path}", headers=headers, json=body)
        assert response.status_code == 409, (path, response.text)
    assert client.get(root, headers=headers).json() == before


def test_postcommit_cache_failure_is_success_and_retry_rebuilds_same_version(publication_client):
    from app.api.dependencies import get_snapshot_cache
    from app.modules.graph_runtime.public import SnapshotCache

    class UnavailableCache(SnapshotCache):
        def get_or_build(self, data):
            raise MemoryError("cache unavailable")

    client, _, headers = publication_client
    draft = prepared_draft(client, headers)
    client.app.dependency_overrides[get_snapshot_cache] = lambda: UnavailableCache()
    response = publish(client, headers, draft)
    assert response.status_code == 200, response.text
    assert response.json()["published"] is True
    assert response.json()["runtime_ready"] is False
    assert client.get("/health/ready").status_code == 503
    client.app.dependency_overrides.pop(get_snapshot_cache)
    client.app.state.snapshot_cache = SnapshotCache()
    assert client.get("/health/ready").status_code == 200
    assert publish(client, headers, draft, active=1).status_code == 409


@pytest.mark.parametrize("failure", ["build", "commit"])
def test_precommit_failure_preserves_previous_pointer_and_retry_is_safe(
    publication_client,
    failure,
):
    from sqlalchemy import event, text
    from sqlalchemy.orm import Session

    from app.modules.graph_admin.public import pin_active_version
    from app.modules.graph_runtime.public import SnapshotCache

    client, engine, headers = publication_client
    first = prepared_draft(client, headers, "baseline")
    assert publish(client, headers, first).status_code == 200
    second = prepared_draft(client, headers, "retryable")
    if failure == "build":
        # 测试夹具模拟存储损坏；验收仍通过发布/固定版本公开面观察。
        edge = second["detail"]["relations"][0]
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE graph_relation SET target_concept_id=999999999 WHERE id=:id"),
                {"id": edge["id"]},
            )
        assert publish(client, headers, second, active=1).status_code == 422
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE graph_relation SET target_concept_id=:target WHERE id=:id"),
                {"id": edge["id"], "target": edge["target_concept_id"]},
            )
    else:

        def fail_commit(connection):
            raise RuntimeError("injected storage commit failure")

        event.listen(engine, "commit", fail_commit)
        try:
            assert publish(client, headers, second, active=1).status_code == 500
        finally:
            event.remove(engine, "commit", fail_commit)
    with Session(engine) as session:
        previous = pin_active_version(session, cache=SnapshotCache())
    assert previous.version_id == int(first["draft_id"])
    assert previous.active_revision == 1
    detail = client.get(f"/api/v1/admin/graph/drafts/{second['draft_id']}", headers=headers).json()
    assert detail["state"] == "DRAFT"
    versions = client.get("/api/v1/admin/graph/versions", headers=headers).json()["items"]
    assert (
        next(v for v in versions if v["version_id"] == second["draft_id"])["content_hash"] is None
    )
    assert publish(client, headers, second, active=1).status_code == 200


def test_publication_uses_the_locked_revision_after_an_earlier_authentication_read(
    publication_client,
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sqlalchemy import event
    from sqlalchemy.orm import Session

    from app.modules.graph_admin.public import pin_active_version

    client, engine, headers = publication_client
    draft = prepared_draft(client, headers)
    started, release = Event(), Event()
    relation = draft["detail"]["relations"][0]

    def pause_publisher(conn, cursor, statement, parameters, context, many):
        if "FROM active_graph" in statement and "FOR UPDATE" in statement and not started.is_set():
            started.set()
            assert release.wait(10)

    event.listen(engine, "before_cursor_execute", pause_publisher)
    try:
        with ThreadPoolExecutor(max_workers=1) as workers:
            pending = workers.submit(publish, client, headers, {**draft, "revision": 3})
            try:
                assert started.wait(5)
                changed = client.post(
                    f"/api/v1/admin/graph/drafts/{draft['draft_id']}/relations/{relation['id']}/update",
                    headers=headers,
                    json={"expected_revision": 2, "rationale": "new review evidence"},
                )
                assert changed.status_code == 200, changed.text
                checked = client.post(
                    f"/api/v1/admin/graph/drafts/{draft['draft_id']}/validate",
                    headers=headers,
                    json={"expected_revision": 3},
                )
                assert checked.status_code == 200, checked.text
            finally:
                release.set()
            response = pending.result(timeout=15)
    finally:
        release.set()
        event.remove(engine, "before_cursor_execute", pause_publisher)
    assert response.status_code == 200, response.text
    with Session(engine) as session:
        pinned = pin_active_version(session, cache=client.app.state.snapshot_cache)
    assert pinned.data.edges[0].review_basis == "new review evidence"
    assert pinned.snapshot.edges == pinned.data.edges


def test_new_version_rebuild_failure_never_serves_the_cached_old_graph(publication_client):
    from app.api.dependencies import get_snapshot_cache
    from app.modules.graph_runtime.public import SnapshotCache

    client, _, headers = publication_client
    first = prepared_draft(client, headers, "cached-old")
    assert publish(client, headers, first).status_code == 200
    old_summary = client.get("/api/v1/graph/active", headers=headers).json()
    second = prepared_draft(client, headers, "unavailable-new")

    class NewVersionUnavailable(SnapshotCache):
        def get_or_build(self, data):
            if data.version_id == int(second["draft_id"]):
                raise MemoryError("new version cache unavailable")
            return super().get_or_build(data)

    cache = NewVersionUnavailable()
    client.app.dependency_overrides[get_snapshot_cache] = lambda: cache
    historical = client.get(f"/api/v1/graph/versions/{first['draft_id']}", headers=headers)
    assert historical.status_code == 200
    assert len(cache) == 1
    response = publish(client, headers, second, active=1)
    assert response.status_code == 200
    assert response.json()["runtime_ready"] is False
    for path in ("/health/ready", "/api/v1/graph/active", "/api/v1/concepts"):
        assert client.get(path, headers=headers).status_code == 503
    client.app.dependency_overrides.pop(get_snapshot_cache)
    summary = client.get("/api/v1/graph/active", headers=headers).json()
    assert summary["version_id"] == second["draft_id"] != old_summary["version_id"]
    assert summary["active_revision"] == 2


def test_storage_unavailable_during_cold_rebuild_returns_503(publication_client):
    from sqlalchemy import event
    from sqlalchemy.exc import SQLAlchemyError

    from app.modules.graph_runtime.public import SnapshotCache

    client, engine, headers = publication_client
    draft = prepared_draft(client, headers)
    assert publish(client, headers, draft).status_code == 200
    client.app.state.snapshot_cache = SnapshotCache()

    def fail_version_read(conn, cursor, statement, parameters, context, many):
        if "FROM graph_version" in statement:
            raise SQLAlchemyError("storage temporarily unavailable")

    event.listen(engine, "before_cursor_execute", fail_version_read)
    try:
        response = client.get("/api/v1/graph/active", headers=headers)
    finally:
        event.remove(engine, "before_cursor_execute", fail_version_read)
    assert response.status_code == 503, response.text
    assert response.json()["code"] == "SERVICE_UNAVAILABLE"
    assert client.get("/api/v1/graph/active", headers=headers).status_code == 200


def test_existing_active_pointer_serializes_competing_replacements(publication_client):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy import event

    client, engine, headers = publication_client
    original = prepared_draft(client, headers, "existing")
    assert publish(client, headers, original).status_code == 200
    first = prepared_draft(client, headers, "replace-first")
    second = prepared_draft(client, headers, "replace-second")
    rendezvous = Barrier(2, timeout=8)

    def synchronize_before_lock(conn, cursor, statement, parameters, context, many):
        if "FROM active_graph" in statement and "FOR UPDATE" in statement:
            rendezvous.wait()

    event.listen(engine, "before_cursor_execute", synchronize_before_lock)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            a = workers.submit(publish, client, headers, first, 1)
            b = workers.submit(publish, client, headers, second, 1)
            responses = [a.result(timeout=20), b.result(timeout=20)]
    finally:
        event.remove(engine, "before_cursor_execute", synchronize_before_lock)
    assert sorted(r.status_code for r in responses) == [200, 409]
    summary = client.get("/api/v1/graph/active", headers=headers).json()
    assert summary["active_revision"] == 2
    assert summary["version_id"] in {first["draft_id"], second["draft_id"]}
    assert publish(client, headers, original, active=1).status_code == 409


def test_new_application_instance_rebuilds_the_same_published_graph(publication_client):
    from fastapi.testclient import TestClient

    from app.infra.db import get_engine
    from app.main import create_app

    client, engine, headers = publication_client
    draft = prepared_draft(client, headers)
    assert publish(client, headers, draft).status_code == 200
    expected = client.get("/api/v1/graph/active", headers=headers).json()
    restarted = create_app()
    restarted.dependency_overrides[get_engine] = lambda: engine
    assert len(restarted.state.snapshot_cache) == 0
    with TestClient(restarted, raise_server_exceptions=False) as fresh_client:
        assert fresh_client.get("/health/ready").status_code == 200
        assert fresh_client.get("/api/v1/graph/active", headers=headers).json() == expected
    assert len(restarted.state.snapshot_cache) == 1


def test_publication_requires_the_current_validation_and_rechecks_review_state(publication_client):
    from sqlalchemy import text

    client, engine, headers = publication_client
    created = client.post(
        "/api/v1/admin/graph/drafts", headers=headers, json={"version_label": "unchecked"}
    ).json()
    draft = {"draft_id": created["draft_id"], "revision": 0}
    assert publish(client, headers, draft).status_code == 409
    assert (
        client.post(
            f"/api/v1/admin/graph/drafts/{draft['draft_id']}/validate",
            headers=headers,
            json={"expected_revision": 0},
        ).status_code
        == 200
    )
    assert publish(client, headers, draft).status_code == 200
    second = prepared_draft(client, headers, "review-corruption")
    edge_id = second["detail"]["relations"][0]["id"]
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE graph_relation SET review_status='PENDING' WHERE id=:id"), {"id": edge_id}
        )
    assert publish(client, headers, second, active=1).status_code == 422
    assert (
        client.get("/api/v1/graph/active", headers=headers).json()["version_id"]
        == draft["draft_id"]
    )
