"""Public HTTP helpers for publication and published-content tests."""

from fastapi.testclient import TestClient

from support.db import BACKEND_ROOT


def prepared_draft(client: TestClient, headers: dict[str, str], label: str = "v1") -> dict:
    root = BACKEND_ROOT.parent
    staged = client.post(
        "/api/v1/admin/imports",
        headers=headers,
        files={
            "node_file": ("nodes.csv", (root / "datamining_kg_nodes.csv").read_bytes()),
            "edge_file": ("edges.csv", (root / "datamining_kg_edges.csv").read_bytes()),
        },
    )
    assert staged.status_code == 201, staged.text
    created = client.post(
        "/api/v1/admin/graph/drafts", headers=headers, json={"version_label": label}
    )
    assert created.status_code == 201, created.text
    draft_id = created.json()["draft_id"]
    accepted = client.post(
        f"/api/v1/admin/graph/drafts/{draft_id}/imports",
        headers=headers,
        json={"batch_id": staged.json()["batch_id"], "expected_revision": 0},
    )
    assert accepted.status_code == 200, accepted.text
    detail = client.get(f"/api/v1/admin/graph/drafts/{draft_id}", headers=headers).json()
    reviewed = client.post(
        f"/api/v1/admin/graph/drafts/{draft_id}/relations/review",
        headers=headers,
        json={
            "relation_ids": [r["id"] for r in detail["relations"]],
            "review_status": "APPROVED",
            "rationale": "课程先修依赖核验通过",
            "expected_revision": detail["draft_revision"],
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    revision = reviewed.json()["new_revision"]
    validated = client.post(
        f"/api/v1/admin/graph/drafts/{draft_id}/validate",
        headers=headers,
        json={"expected_revision": revision},
    )
    assert validated.status_code == 200, validated.text
    return {"draft_id": draft_id, "revision": revision, "detail": detail}


def publish(client: TestClient, headers: dict[str, str], draft: dict, active: int = 0):
    return client.post(
        f"/api/v1/admin/graph/drafts/{draft['draft_id']}/publish",
        headers=headers,
        json={"expected_revision": draft["revision"], "expected_active_revision": active},
    )
