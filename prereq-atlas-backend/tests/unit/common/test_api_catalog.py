"""冻结接口目录：39 个接口、GET/POST 白名单、权限与错误定义。"""

from __future__ import annotations

import re

import pytest

from app.common.api_catalog import (
    ENDPOINTS,
    HTTP_METHOD_WHITELIST,
    LEGACY_HEALTH_PATH,
    SRC_SCENARIO_CASES,
    Endpoint,
)
from app.common.contracts import ERROR_STATUS, AuthLevel

EXPECTED: set[tuple[str, str, str, str, bool]] = {
    ("POST", "/auth/register", "P", "T05", True),
    ("POST", "/auth/token", "P", "T06", True),
    ("GET", "/users/me", "L", "T06", False),
    ("GET", "/graph/active", "L", "T16", False),
    ("GET", "/graph/versions/{version_id}", "L", "T16", False),
    ("GET", "/concepts", "L", "T16", False),
    ("GET", "/concepts/{concept_id}", "L", "T16", False),
    ("GET", "/me/mastery", "S", "T19", False),
    ("POST", "/me/mastery/{concept_id}/declare", "S", "T19", True),
    ("POST", "/me/mastery/{concept_id}/cancel", "S", "T19", True),
    ("POST", "/me/plans", "S", "T20", True),
    ("GET", "/me/plans", "S", "T21", False),
    ("GET", "/me/plans/{plan_id}", "S", "T21", False),
    ("POST", "/me/plans/{plan_id}/nodes/{concept_id}/progress", "S", "T22", True),
    ("POST", "/me/plans/{plan_id}/replan", "S", "T31", True),
    ("POST", "/me/plans/{plan_id}/delete", "S", "T32", True),
    ("POST", "/admin/imports", "A", "T08", True),
    ("GET", "/admin/imports/{batch_id}", "A", "T08", False),
    ("GET", "/admin/graph/versions", "A", "T10", False),
    ("POST", "/admin/graph/drafts", "A", "T10", True),
    ("GET", "/admin/graph/drafts/{draft_id}", "A", "T10", False),
    ("POST", "/admin/graph/drafts/{draft_id}/imports", "A", "T11", True),
    ("POST", "/admin/graph/drafts/{draft_id}/relations", "A", "T12", True),
    ("POST", "/admin/graph/drafts/{draft_id}/relations/{relation_id}/update", "A", "T12", True),
    ("POST", "/admin/graph/drafts/{draft_id}/relations/review", "A", "T12", True),
    ("POST", "/admin/graph/drafts/{draft_id}/relations/{relation_id}/delete", "A", "T12", True),
    ("POST", "/admin/graph/drafts/{draft_id}/validate", "A", "T14", True),
    ("POST", "/admin/graph/drafts/{draft_id}/publish", "A", "T15", True),
    ("POST", "/admin/graph/drafts/{draft_id}/chapters/{chapter_code}/save", "A", "T33", True),
    ("POST", "/admin/graph/drafts/{draft_id}/chapters/{chapter_code}/delete", "A", "T33", True),
    ("POST", "/admin/graph/drafts/{draft_id}/concepts", "A", "T33", True),
    ("POST", "/admin/graph/drafts/{draft_id}/concepts/{concept_id}/update", "A", "T33", True),
    ("POST", "/admin/graph/drafts/{draft_id}/concepts/{concept_id}/delete", "A", "T33", True),
    ("POST", "/admin/graph/drafts/{draft_id}/resources", "A", "T34", True),
    ("POST", "/admin/graph/drafts/{draft_id}/resources/{resource_id}/update", "A", "T34", True),
    ("POST", "/admin/graph/drafts/{draft_id}/resources/{resource_id}/delete", "A", "T34", True),
    ("GET", "/admin/graph/versions/{version_id}/audit", "A", "T36", False),
    ("GET", "/health/live", "P", "T04", False),
    ("GET", "/health/ready", "P", "T15", False),
}


def test_catalog_has_exactly_the_frozen_39_endpoints() -> None:
    assert len(ENDPOINTS) == 39
    actual = {
        (endpoint.method, endpoint.path, endpoint.auth.value, endpoint.task, endpoint.mutating)
        for endpoint in ENDPOINTS
    }
    assert actual == EXPECTED


def test_catalog_has_no_duplicate_method_path() -> None:
    keys = [(endpoint.method, endpoint.path) for endpoint in ENDPOINTS]
    assert len(keys) == len(set(keys))


def test_only_get_and_post_are_allowed() -> None:
    assert frozenset({"GET", "POST"}) == HTTP_METHOD_WHITELIST
    assert {endpoint.method for endpoint in ENDPOINTS} <= HTTP_METHOD_WHITELIST
    for endpoint in ENDPOINTS:
        assert endpoint.method in HTTP_METHOD_WHITELIST


def test_legacy_health_is_removed_but_live_and_ready_remain() -> None:
    paths = {endpoint.path for endpoint in ENDPOINTS}
    assert LEGACY_HEALTH_PATH == "/health"
    assert LEGACY_HEALTH_PATH not in paths
    assert "/health/live" in paths
    assert "/health/ready" in paths


def test_state_changing_operations_are_post_only() -> None:
    for endpoint in ENDPOINTS:
        if endpoint.mutating:
            assert endpoint.method == "POST", endpoint
        else:
            assert endpoint.method == "GET", endpoint


def test_public_endpoints_are_exactly_registration_login_and_health() -> None:
    public = {endpoint.path for endpoint in ENDPOINTS if endpoint.auth is AuthLevel.PUBLIC}
    assert public == {"/auth/register", "/auth/token", "/health/live", "/health/ready"}


@pytest.mark.parametrize("endpoint", ENDPOINTS, ids=lambda e: f"{e.method} {e.path}")
def test_each_endpoint_has_task_request_success_and_errors(endpoint: Endpoint) -> None:
    assert re.fullmatch(r"T\d{2}", endpoint.task)
    assert endpoint.request.strip()
    assert endpoint.success.strip()
    assert isinstance(endpoint.errors, tuple)
    if endpoint.path != "/health/live":
        assert endpoint.errors
    for code in endpoint.errors:
        assert code in ERROR_STATUS


def test_admin_and_student_boundaries_are_explicit() -> None:
    admin_paths = {endpoint.path for endpoint in ENDPOINTS if endpoint.auth is AuthLevel.ADMIN}
    student_paths = {endpoint.path for endpoint in ENDPOINTS if endpoint.auth is AuthLevel.STUDENT}
    assert admin_paths and all(path.startswith("/admin/") for path in admin_paths)
    assert student_paths and all(
        path.startswith("/me/") for path in student_paths
    ), student_paths
    assert not (admin_paths & student_paths)


def test_full_path_adds_api_prefix_except_health() -> None:
    by_path = {endpoint.path: endpoint for endpoint in ENDPOINTS}
    assert by_path["/auth/register"].full_path == "/api/v1/auth/register"
    assert by_path["/health/live"].full_path == "/health/live"


def test_src_traceability_covers_all_23_original_scenarios() -> None:
    assert len(SRC_SCENARIO_CASES) == 23
    assert set(SRC_SCENARIO_CASES) == {f"SRC-T{index:02d}" for index in range(1, 24)}
    for cases in SRC_SCENARIO_CASES.values():
        assert cases
        for case in cases:
            assert re.fullmatch(r"T\d{2}(?:-\d{2})?", case), case


def test_write_models_are_not_used_for_get_endpoints() -> None:
    """GET 只查询：接口目录中以 Request 结尾的写模型不得出现在 GET 端点。"""
    for endpoint in ENDPOINTS:
        if endpoint.method == "GET":
            assert "Request" not in endpoint.request, endpoint
