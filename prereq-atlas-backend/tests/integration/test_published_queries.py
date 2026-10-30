"""T16: public published graph and knowledge-card HTTP queries."""

import pytest

from support.publication import prepared_draft, publish

pytestmark = pytest.mark.integration


def test_active_summary_and_full_graph_share_the_published_id_and_hash(publication_client):
    client, _, headers = publication_client
    draft = prepared_draft(client, headers)
    assert publish(client, headers, draft).status_code == 200
    active = client.get("/api/v1/graph/active", headers=headers)
    assert active.status_code == 200, active.text
    summary = active.json()
    assert summary["version_id"] == draft["draft_id"]
    assert summary["active_revision"] == 1
    graph = client.get(f"/api/v1/graph/versions/{draft['draft_id']}", headers=headers)
    assert graph.status_code == 200, graph.text
    body = graph.json()
    assert body["version_id"] == summary["version_id"]
    assert body["content_hash"] == summary["content_hash"]
    assert len(body["nodes"]) == 120
    assert len(body["chapters"]) == 15
    assert len(body["edges"]) == 112
    expected = {
        (r["source_concept_id"], r["target_concept_id"]) for r in draft["detail"]["relations"]
    }
    assert {(e["source_id"], e["target_id"]) for e in body["edges"]} == expected
    assert all(e["necessity"] == "REQUIRED" for e in body["edges"])


def test_search_chapter_and_pagination_resolve_the_active_version_once(publication_client):
    client, _, headers = publication_client
    draft = prepared_draft(client, headers)
    assert publish(client, headers, draft).status_code == 200
    first = client.get("/api/v1/concepts", headers=headers, params={"page_size": 1})
    assert first.status_code == 200, first.text
    assert first.json()["total"] == 120
    assert len(first.json()["items"]) == 1
    assert first.json()["items"][0]["code"] == "N001"
    second = client.get(
        "/api/v1/concepts", headers=headers, params={"page": 2, "page_size": 1}
    ).json()
    assert second["items"][0]["code"] == "N002"
    hundred = client.get("/api/v1/concepts", headers=headers, params={"page_size": 100}).json()
    assert len(hundred["items"]) == 100
    assert (
        client.get(
            "/api/v1/concepts", headers=headers, params={"page": 2, "page_size": 100}
        ).json()["items"][-1]["code"]
        == "N120"
    )
    for q in ("算术平均数", "Arithmetic mean", "N001"):
        result = client.get("/api/v1/concepts", headers=headers, params={"q": q}).json()
        assert result["total"] == 1
        assert result["items"][0]["code"] == "N001"
    chapter = client.get(
        "/api/v1/concepts", headers=headers, params={"chapter": "M01", "page_size": 100}
    ).json()
    assert chapter["total"] == 17
    assert all(n["content"]["chapter_code"] == "M01" for n in chapter["items"])
    assert (
        client.get("/api/v1/concepts", headers=headers, params={"q": "不存在"}).json()["items"]
        == []
    )
    assert (
        client.get(
            "/api/v1/concepts", headers=headers, params={"chapter": "M02", "q": "N001"}
        ).json()["total"]
        == 0
    )
    for params in ({"page": 0}, {"page_size": 0}, {"page_size": 101}, {"q": "x" * 101}):
        assert client.get("/api/v1/concepts", headers=headers, params=params).status_code == 422


def test_knowledge_card_has_direct_relations_and_empty_optional_content(publication_client):
    client, _, headers = publication_client
    draft = prepared_draft(client, headers)
    assert publish(client, headers, draft).status_code == 200
    detail = draft["detail"]
    first_edge = detail["relations"][0]
    concept_id = first_edge["source_concept_id"]
    response = client.get(f"/api/v1/concepts/{concept_id}", headers=headers)
    assert response.status_code == 200, response.text
    card = response.json()
    assert card["version_id"] == draft["draft_id"]
    assert card["concept_id"] == concept_id
    assert card["content"]["introduction"] is None
    assert card["content"]["learning_focus"] is None
    assert card["resources"] == []
    assert {r["concept_id"] for r in card["prerequisites"]} == {
        e["source_concept_id"] for e in detail["relations"] if e["target_concept_id"] == concept_id
    }
    assert {r["concept_id"] for r in card["successors"]} == {
        e["target_concept_id"] for e in detail["relations"] if e["source_concept_id"] == concept_id
    }
    isolated = next(n for n in detail["concepts"] if n["code"] == "N120")
    lonely = client.get(f"/api/v1/concepts/{isolated['concept_id']}", headers=headers).json()
    assert lonely["prerequisites"] == lonely["successors"] == []
    assert client.get("/api/v1/concepts/999999", headers=headers).status_code == 404


def test_historical_versions_remain_readable_and_drafts_are_hidden(publication_client):
    client, _, headers = publication_client
    first = prepared_draft(client, headers, "history")
    assert publish(client, headers, first).status_code == 200
    first_graph = client.get(f"/api/v1/graph/versions/{first['draft_id']}", headers=headers).json()
    second = prepared_draft(client, headers, "current")
    for version_id in (second["draft_id"], "999999"):
        for path, params in (
            (f"graph/versions/{version_id}", {}),
            ("concepts", {"version_id": version_id}),
            ("concepts/1", {"version_id": version_id}),
        ):
            assert client.get(f"/api/v1/{path}", headers=headers, params=params).status_code == 404
    assert publish(client, headers, second, active=1).status_code == 200
    assert (
        client.get(f"/api/v1/graph/versions/{first['draft_id']}", headers=headers).json()
        == first_graph
    )
    result = client.get(
        "/api/v1/concepts", headers=headers, params={"version_id": first["draft_id"]}
    ).json()
    assert result["version_id"] == first["draft_id"]
    assert result["content_hash"] == first_graph["content_hash"]
    assert (
        client.get("/api/v1/graph/active", headers=headers).json()["version_id"]
        == second["draft_id"]
    )
    assert (
        client.get("/api/v1/concepts", headers=headers).json()["version_id"] == second["draft_id"]
    )


def test_query_in_flight_keeps_its_pinned_version_while_a_new_one_is_published(publication_client):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sqlalchemy import event

    client, engine, headers = publication_client
    first = prepared_draft(client, headers, "query-old")
    assert publish(client, headers, first).status_code == 200
    second = prepared_draft(client, headers, "query-new")
    started, release = Event(), Event()

    def pause_old_read(conn, cursor, statement, parameters, context, many):
        if "FROM chapter_revision" in statement and not started.is_set():
            started.set()
            assert release.wait(10), "publication did not release the pending query"

    event.listen(engine, "after_cursor_execute", pause_old_read)
    try:
        with ThreadPoolExecutor(max_workers=1) as workers:
            pending = workers.submit(
                client.get, "/api/v1/concepts", headers=headers, params={"page_size": 100}
            )
            try:
                assert started.wait(5)
                assert publish(client, headers, second, active=1).status_code == 200
            finally:
                release.set()
            old_result = pending.result(timeout=15)
    finally:
        release.set()
        event.remove(engine, "after_cursor_execute", pause_old_read)
    assert old_result.status_code == 200, old_result.text
    assert old_result.json()["version_id"] == first["draft_id"]
    assert len(old_result.json()["items"]) == 100
    assert (
        client.get("/api/v1/concepts", headers=headers).json()["version_id"] == second["draft_id"]
    )


def test_authenticated_students_can_read_but_cannot_publish(publication_client):
    client, _, headers = publication_client
    draft = prepared_draft(client, headers)
    assert publish(client, headers, draft).status_code == 200
    credentials = {"username": "Reader_Student", "password": "Read-pass-123"}
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
    login = client.post("/api/v1/auth/token", data=credentials)
    student_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    concept_id = draft["detail"]["concepts"][0]["concept_id"]
    for path in (
        "graph/active",
        f"graph/versions/{draft['draft_id']}",
        "concepts",
        f"concepts/{concept_id}",
    ):
        assert client.get(f"/api/v1/{path}", headers=student_headers).status_code == 200
        assert client.get(f"/api/v1/{path}").status_code == 401
    assert publish(client, student_headers, draft, active=1).status_code == 403
    assert publish(client, {}, draft, active=1).status_code == 401


def test_missing_graph_and_invalid_version_queries_have_precise_errors(publication_client):
    client, _, headers = publication_client
    assert client.get("/api/v1/graph/active", headers=headers).status_code == 503
    assert client.get("/api/v1/concepts", headers=headers).status_code == 503
    assert client.get("/api/v1/concepts/1", headers=headers).status_code == 503
    assert client.get("/api/v1/graph/versions/999", headers=headers).status_code == 404
    for value in ("0", "-1", "text", "18446744073709551616"):
        assert client.get(f"/api/v1/graph/versions/{value}", headers=headers).status_code == 422
        assert (
            client.get(
                "/api/v1/concepts", headers=headers, params={"version_id": value}
            ).status_code
            == 422
        )


def test_knowledge_card_preserves_real_resources_and_optional_null_fields(publication_client):
    from sqlalchemy.orm import Session

    from app.modules.catalog.public import (
        ConceptContent,
        DraftWriteContext,
        add_resource,
        ensure_concept,
        write_concept_content,
    )
    from app.modules.graph_admin.public import ensure_draft_writable

    client, engine, headers = publication_client
    created = client.post(
        "/api/v1/admin/graph/drafts", headers=headers, json={"version_label": "minimal-card"}
    )
    draft_id = created.json()["draft_id"]
    # T33/T34 的 HTTP 编辑尚未实现，通过已验收的 catalog 公开写入面准备数据。
    with Session(engine) as session:
        context = DraftWriteContext(int(draft_id), 0, 1, ensure_draft_writable)
        concept_id = ensure_concept(session, context, code="MINIMAL")
        write_concept_content(
            session, context, concept_id=concept_id, content=ConceptContent(label_zh="独立概念")
        )
        add_resource(
            session,
            context,
            concept_id=concept_id,
            title="课程资料",
            url="https://example.org/course",
        )
        session.commit()
    assert (
        client.post(
            f"/api/v1/admin/graph/drafts/{draft_id}/validate",
            headers=headers,
            json={"expected_revision": 0},
        ).status_code
        == 200
    )
    assert publish(client, headers, {"draft_id": draft_id, "revision": 0}).status_code == 200
    response = client.get(f"/api/v1/concepts/{concept_id}", headers=headers)
    assert response.status_code == 200, response.text
    card = response.json()
    assert card["content"] == {
        "label_zh": "独立概念",
        "label_en": None,
        "chapter_code": None,
        "node_type": None,
        "introduction": None,
        "learning_focus": None,
    }
    assert card["prerequisites"] == card["successors"] == []
    assert len(card["resources"]) == 1
    assert card["resources"][0]["title"] == "课程资料"
    assert card["resources"][0]["url"] == "https://example.org/course"
    assert card["resources"][0]["source"] is None
