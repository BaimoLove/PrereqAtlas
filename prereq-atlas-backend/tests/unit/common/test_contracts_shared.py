"""共享枚举、Actor、错误契约与保护值常量。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.common.contracts import (
    BATCH_REVIEW_MAX,
    BATCH_REVIEW_MIN,
    CHAPTER_CODE_MAX,
    CHAPTER_NAME_MAX,
    CONCEPT_CODE_MAX,
    DEFAULT_PLAN_TITLE,
    ERROR_STATUS,
    IMPORT_MAX_BYTES,
    IMPORT_MAX_RECORDS,
    LABEL_EN_MAX,
    LABEL_ZH_MAX,
    PASSWORD_MAX,
    PASSWORD_MIN,
    RATIONALE_MAX,
    RATIONALE_MIN,
    SEARCH_MAX,
    SOURCE_NOTE_MAX,
    TARGET_MAX,
    TARGET_MIN,
    TEXT_MAX,
    TITLE_MAX,
    TITLE_MIN,
    URL_MAX,
    USERNAME_MAX,
    USERNAME_MIN,
    USERNAME_PATTERN,
    VERSION_LABEL_MAX,
    Actor,
    AuthLevel,
    ConcurrencyErrorDetails,
    ErrorCode,
    ErrorResponse,
    FieldError,
    GraphState,
    ImportStatus,
    MasteryVisibility,
    MissingTargetsDetails,
    Necessity,
    PlanOutcome,
    ProgressStatus,
    ReasonCode,
    ReferenceErrorDetails,
    ReviewStatus,
    Role,
    TargetStatus,
)


def test_enum_values_are_frozen_to_the_plan() -> None:
    assert [item.value for item in Role] == ["STUDENT", "ADMIN"]
    assert [item.value for item in AuthLevel] == ["P", "L", "S", "A"]
    assert [item.value for item in GraphState] == ["DRAFT", "PUBLISHED"]
    assert [item.value for item in ImportStatus] == ["STAGED", "FAILED", "CONSUMED"]
    assert [item.value for item in Necessity] == ["REQUIRED", "RECOMMENDED", "OPTIONAL"]
    assert [item.value for item in ReviewStatus] == ["PENDING", "APPROVED", "REJECTED"]
    assert [item.value for item in ProgressStatus] == [
        "NOT_STARTED",
        "IN_PROGRESS",
        "COMPLETED",
    ]
    assert [item.value for item in MasteryVisibility] == ["VISIBLE", "NOT_VISIBLE", "UNKNOWN"]
    assert [item.value for item in TargetStatus] == ["MASTERED", "NEEDS_REVIEW"]
    assert [item.value for item in PlanOutcome] == ["REVIEW_REQUIRED", "NO_REVIEW_NEEDED"]
    assert [item.value for item in ReasonCode] == [
        "TARGET",
        "PREREQUISITE",
        "MASTERED_BOUNDARY",
    ]


def test_actor_requires_positive_user_and_known_role() -> None:
    actor = Actor(user_id=1, role=Role.STUDENT, token_version=0)
    assert actor.role is Role.STUDENT
    for payload in (
        {"user_id": 0, "role": "STUDENT", "token_version": 0},
        {"user_id": 1, "role": "TEACHER", "token_version": 0},
        {"user_id": 1, "role": "STUDENT", "token_version": -1},
        {"user_id": 1, "role": "STUDENT", "token_version": 0, "owner_id": "9"},
    ):
        with pytest.raises(ValidationError):
            Actor(**payload)


def test_error_code_status_mapping_is_total_and_matches_plan() -> None:
    assert {code.value for code in ErrorCode} == {code.value for code in ERROR_STATUS}
    assert ERROR_STATUS[ErrorCode.AUTHENTICATION_REQUIRED] == 401
    assert ERROR_STATUS[ErrorCode.FORBIDDEN] == 403
    assert ERROR_STATUS[ErrorCode.NOT_FOUND] == 404
    assert ERROR_STATUS[ErrorCode.METHOD_NOT_ALLOWED] == 405
    assert ERROR_STATUS[ErrorCode.CONFLICT] == 409
    assert ERROR_STATUS[ErrorCode.PAYLOAD_TOO_LARGE] == 413
    assert ERROR_STATUS[ErrorCode.VALIDATION_ERROR] == 422
    assert ERROR_STATUS[ErrorCode.SERVICE_UNAVAILABLE] == 503
    assert ERROR_STATUS[ErrorCode.INTERNAL_ERROR] == 500


def test_error_response_shape_matches_contract() -> None:
    response = ErrorResponse(
        code=ErrorCode.VALIDATION_ERROR,
        message="输入不合法",
        details={"field": "title", "reason": "去空白后不能为空"},
        request_id="7f6f0a52-0d5c-4a63-9a4c-2a8d1f0e5b11",
    )
    assert response.model_dump() == {
        "code": "VALIDATION_ERROR",
        "message": "输入不合法",
        "details": {"field": "title", "reason": "去空白后不能为空"},
        "request_id": "7f6f0a52-0d5c-4a63-9a4c-2a8d1f0e5b11",
    }
    with pytest.raises(ValidationError):
        ErrorResponse(
            code="NOT_A_CODE",
            message="x",
            details=None,
            request_id="id",
        )


def test_structured_error_details_shapes() -> None:
    assert FieldError(field="password", reason="长度 8～128").model_dump() == {
        "field": "password",
        "reason": "长度 8～128",
    }
    assert ReferenceErrorDetails(resource_type="chapter", ids=["3"]).model_dump() == {
        "resource_type": "chapter",
        "ids": ["3"],
    }
    assert ConcurrencyErrorDetails(expected_revision=2, actual_revision=3).model_dump() == {
        "expected_revision": 2,
        "actual_revision": 3,
    }
    assert MissingTargetsDetails(missing_target_ids=["7", "9"]).model_dump() == {
        "missing_target_ids": ["7", "9"]
    }
    with pytest.raises(ValidationError):
        ReferenceErrorDetails(resource_type="chapter", ids=[3])  # type: ignore[list-item]


def test_protection_constants_match_the_frozen_table() -> None:
    assert (TITLE_MIN, TITLE_MAX) == (1, 100)
    assert DEFAULT_PLAN_TITLE == "复习方案"
    assert (TARGET_MIN, TARGET_MAX) == (1, 20)
    assert (BATCH_REVIEW_MIN, BATCH_REVIEW_MAX) == (1, 112)
    assert (USERNAME_MIN, USERNAME_MAX) == (3, 32)
    assert USERNAME_PATTERN == r"^[A-Za-z0-9_]+$"
    assert (PASSWORD_MIN, PASSWORD_MAX) == (6, 128)
    assert IMPORT_MAX_BYTES == 10 * 1024 * 1024
    assert IMPORT_MAX_RECORDS == 10000
    assert SEARCH_MAX == 100
    assert TEXT_MAX == 10000
    assert URL_MAX == 1000
    assert SOURCE_NOTE_MAX == 255
    assert (RATIONALE_MIN, RATIONALE_MAX) == (1, 500)
    assert VERSION_LABEL_MAX == 64
    assert CHAPTER_NAME_MAX == 100
    assert CHAPTER_CODE_MAX == 32
    assert CONCEPT_CODE_MAX == 64
    assert (LABEL_ZH_MAX, LABEL_EN_MAX) == (200, 200)


def test_error_response_does_not_accept_arbitrary_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ErrorResponse(
            code=ErrorCode.INTERNAL_ERROR,
            message="内部错误",
            details=None,
            request_id="id",
            traceback="secret",  # type: ignore[call-arg]
        )
