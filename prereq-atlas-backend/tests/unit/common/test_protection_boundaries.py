"""保护值 N−1/N/N+1 边界表：每个阈值都必须有明确预期。

表中的期望值来自 plan 第 5.1 节的冻结值，不由被测代码推导；
测试只验证契约模型/判定函数与这些期望一致。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.common.contracts import (
    IMPORT_MAX_BYTES,
    IMPORT_MAX_RECORDS,
    PAGE_SIZE_MAX,
    PageParams,
    Revision,
    within_import_byte_limit,
    within_import_record_limit,
)
from app.common.http_contracts import (
    ChapterSaveRequest,
    ConceptCreateRequest,
    ConceptSearchQuery,
    CreateDraftRequest,
    PlanCreateRequest,
    RegisterRequest,
    RelationBatchReviewRequest,
    ResourceCreateRequest,
    ValidateDraftRequest,
)

REQUEST_ID = "7f6f0a52-0d5c-4a63-9a4c-2a8d1f0e5b11"
URL_BASE = "https://example.com/"


def _url_of_length(length: int) -> str:
    return URL_BASE + "a" * (length - len(URL_BASE))


def _valid(model: object) -> bool:
    try:
        model()  # type: ignore[operator]
    except ValidationError:
        return False
    return True


CASES: list[tuple[str, object, bool]] = [
    # page: N=1
    ("page N-1=0", lambda: PageParams(page=0), False),
    ("page N=1", lambda: PageParams(page=1), True),
    ("page N+1=2", lambda: PageParams(page=2), True),
    # page_size: N=1 下界、N=100 上界
    ("page_size 0", lambda: PageParams(page_size=0), False),
    ("page_size 1", lambda: PageParams(page_size=1), True),
    ("page_size 100", lambda: PageParams(page_size=PAGE_SIZE_MAX), True),
    ("page_size 101", lambda: PageParams(page_size=PAGE_SIZE_MAX + 1), False),
    # target_ids: 原始长度 1～20
    ("target 0", lambda: PlanCreateRequest(target_ids=[], request_id=REQUEST_ID), False),
    ("target 1", lambda: PlanCreateRequest(target_ids=["1"], request_id=REQUEST_ID), True),
    (
        "target 20",
        lambda: PlanCreateRequest(target_ids=["1"] * 20, request_id=REQUEST_ID),
        True,
    ),
    (
        "target 21",
        lambda: PlanCreateRequest(target_ids=["1"] * 21, request_id=REQUEST_ID),
        False,
    ),
    # relation_ids: 1～112
    (
        "review 0",
        lambda: RelationBatchReviewRequest(
            relation_ids=[], review_status="APPROVED", rationale="r", expected_revision=0
        ),
        False,
    ),
    (
        "review 1",
        lambda: RelationBatchReviewRequest(
            relation_ids=["1"], review_status="APPROVED", rationale="r", expected_revision=0
        ),
        True,
    ),
    (
        "review 112",
        lambda: RelationBatchReviewRequest(
            relation_ids=["1"] * 112,
            review_status="APPROVED",
            rationale="r",
            expected_revision=0,
        ),
        True,
    ),
    (
        "review 113",
        lambda: RelationBatchReviewRequest(
            relation_ids=["1"] * 113,
            review_status="APPROVED",
            rationale="r",
            expected_revision=0,
        ),
        False,
    ),
    # title: 1～100
    (
        "title 0",
        lambda: PlanCreateRequest(target_ids=["1"], request_id=REQUEST_ID, title="   "),
        False,
    ),
    (
        "title 1",
        lambda: PlanCreateRequest(target_ids=["1"], request_id=REQUEST_ID, title="x"),
        True,
    ),
    (
        "title 100",
        lambda: PlanCreateRequest(target_ids=["1"], request_id=REQUEST_ID, title="x" * 100),
        True,
    ),
    (
        "title 101",
        lambda: PlanCreateRequest(target_ids=["1"], request_id=REQUEST_ID, title="x" * 101),
        False,
    ),
    # 搜索词: 0～100（空串表示全部）
    ("q 0", lambda: ConceptSearchQuery(version_id="1", q=""), True),
    ("q 99", lambda: ConceptSearchQuery(version_id="1", q="x" * 99), True),
    ("q 100", lambda: ConceptSearchQuery(version_id="1", q="x" * 100), True),
    ("q 101", lambda: ConceptSearchQuery(version_id="1", q="x" * 101), False),
    # username: 3～32
    ("username 2", lambda: RegisterRequest(username="ab", password="12345678"), False),
    ("username 3", lambda: RegisterRequest(username="abc", password="12345678"), True),
    (
        "username 32",
        lambda: RegisterRequest(username="a" * 32, password="12345678"),
        True,
    ),
    (
        "username 33",
        lambda: RegisterRequest(username="a" * 33, password="12345678"),
        False,
    ),
    # password: 8～128
    ("password 7", lambda: RegisterRequest(username="abc", password="p" * 7), False),
    ("password 8", lambda: RegisterRequest(username="abc", password="p" * 8), True),
    (
        "password 128",
        lambda: RegisterRequest(username="abc", password="p" * 128),
        True,
    ),
    (
        "password 129",
        lambda: RegisterRequest(username="abc", password="p" * 129),
        False,
    ),
    # version_label: 1～64
    ("version_label 0", lambda: CreateDraftRequest(version_label="  "), False),
    ("version_label 63", lambda: CreateDraftRequest(version_label="v" * 63), True),
    ("version_label 64", lambda: CreateDraftRequest(version_label="v" * 64), True),
    ("version_label 65", lambda: CreateDraftRequest(version_label="v" * 65), False),
    # chapter name: 1～100
    ("chapter_name 0", lambda: ChapterSaveRequest(name="  ", expected_revision=0), False),
    (
        "chapter_name 99",
        lambda: ChapterSaveRequest(name="c" * 99, expected_revision=0),
        True,
    ),
    (
        "chapter_name 100",
        lambda: ChapterSaveRequest(name="c" * 100, expected_revision=0),
        True,
    ),
    (
        "chapter_name 101",
        lambda: ChapterSaveRequest(name="c" * 101, expected_revision=0),
        False,
    ),
    # chapter_code: 1～32
    (
        "chapter_code 31",
        lambda: ConceptCreateRequest(
            code="N001", label_zh="x", chapter_code="M" * 31, expected_revision=0
        ),
        True,
    ),
    (
        "chapter_code 32",
        lambda: ConceptCreateRequest(
            code="N001", label_zh="x", chapter_code="M" * 32, expected_revision=0
        ),
        True,
    ),
    (
        "chapter_code 33",
        lambda: ConceptCreateRequest(
            code="N001", label_zh="x", chapter_code="M" * 33, expected_revision=0
        ),
        False,
    ),
    # concept code: 1～64
    (
        "code 63",
        lambda: ConceptCreateRequest(code="N" * 63, label_zh="x", expected_revision=0),
        True,
    ),
    (
        "code 64",
        lambda: ConceptCreateRequest(code="N" * 64, label_zh="x", expected_revision=0),
        True,
    ),
    (
        "code 65",
        lambda: ConceptCreateRequest(code="N" * 65, label_zh="x", expected_revision=0),
        False,
    ),
    # label_zh: 1～200
    (
        "label_zh 199",
        lambda: ConceptCreateRequest(code="N001", label_zh="x" * 199, expected_revision=0),
        True,
    ),
    (
        "label_zh 200",
        lambda: ConceptCreateRequest(code="N001", label_zh="x" * 200, expected_revision=0),
        True,
    ),
    (
        "label_zh 201",
        lambda: ConceptCreateRequest(code="N001", label_zh="x" * 201, expected_revision=0),
        False,
    ),
    # label_en: ≤200
    (
        "label_en 199",
        lambda: ConceptCreateRequest(
            code="N001", label_zh="x", label_en="e" * 199, expected_revision=0
        ),
        True,
    ),
    (
        "label_en 200",
        lambda: ConceptCreateRequest(
            code="N001", label_zh="x", label_en="e" * 200, expected_revision=0
        ),
        True,
    ),
    (
        "label_en 201",
        lambda: ConceptCreateRequest(
            code="N001", label_zh="x", label_en="e" * 201, expected_revision=0
        ),
        False,
    ),
    # introduction/learning_focus: ≤10000
    (
        "introduction 9999",
        lambda: ConceptCreateRequest(
            code="N001", label_zh="x", introduction="i" * 9999, expected_revision=0
        ),
        True,
    ),
    (
        "introduction 10000",
        lambda: ConceptCreateRequest(
            code="N001", label_zh="x", introduction="i" * 10000, expected_revision=0
        ),
        True,
    ),
    (
        "introduction 10001",
        lambda: ConceptCreateRequest(
            code="N001", label_zh="x", introduction="i" * 10001, expected_revision=0
        ),
        False,
    ),
    # rationale: 1～500（批量审核理由必填）
    (
        "rationale 0",
        lambda: RelationBatchReviewRequest(
            relation_ids=["1"], review_status="APPROVED", rationale="  ", expected_revision=0
        ),
        False,
    ),
    (
        "rationale 1",
        lambda: RelationBatchReviewRequest(
            relation_ids=["1"], review_status="APPROVED", rationale="r", expected_revision=0
        ),
        True,
    ),
    (
        "rationale 500",
        lambda: RelationBatchReviewRequest(
            relation_ids=["1"],
            review_status="APPROVED",
            rationale="r" * 500,
            expected_revision=0,
        ),
        True,
    ),
    (
        "rationale 501",
        lambda: RelationBatchReviewRequest(
            relation_ids=["1"],
            review_status="APPROVED",
            rationale="r" * 501,
            expected_revision=0,
        ),
        False,
    ),
    # url: ≤1000，且必须 http/https
    (
        "url 999",
        lambda: ResourceCreateRequest(
            concept_id="1", title="t", url=_url_of_length(999), expected_revision=0
        ),
        True,
    ),
    (
        "url 1000",
        lambda: ResourceCreateRequest(
            concept_id="1", title="t", url=_url_of_length(1000), expected_revision=0
        ),
        True,
    ),
    (
        "url 1001",
        lambda: ResourceCreateRequest(
            concept_id="1", title="t", url=_url_of_length(1001), expected_revision=0
        ),
        False,
    ),
    # display_order: 0～2^32-1
    (
        "display_order -1",
        lambda: ChapterSaveRequest(name="x", display_order=-1, expected_revision=0),
        False,
    ),
    (
        "display_order 0",
        lambda: ChapterSaveRequest(name="x", display_order=0, expected_revision=0),
        True,
    ),
    (
        "display_order 1",
        lambda: ChapterSaveRequest(name="x", display_order=1, expected_revision=0),
        True,
    ),
    (
        "display_order 2^32-1",
        lambda: ChapterSaveRequest(name="x", display_order=2**32 - 1, expected_revision=0),
        True,
    ),
    (
        "display_order 2^32",
        lambda: ChapterSaveRequest(name="x", display_order=2**32, expected_revision=0),
        False,
    ),
    # expected_revision: 0～2^53-1
    ("expected_revision -1", lambda: ValidateDraftRequest(expected_revision=-1), False),
    ("expected_revision 0", lambda: ValidateDraftRequest(expected_revision=0), True),
    ("expected_revision 1", lambda: ValidateDraftRequest(expected_revision=1), True),
    (
        "expected_revision 2^53-1",
        lambda: ValidateDraftRequest(expected_revision=2**53 - 1),
        True,
    ),
    (
        "expected_revision 2^53",
        lambda: ValidateDraftRequest(expected_revision=2**53),
        False,
    ),
]


@pytest.mark.parametrize(("case", "build", "expected"), CASES, ids=[c[0] for c in CASES])
def test_protection_boundary(case: str, build: object, expected: bool) -> None:
    assert _valid(build) is expected


def test_import_byte_limit_boundaries() -> None:
    assert within_import_byte_limit(0) is False
    assert within_import_byte_limit(1) is True
    assert within_import_byte_limit(IMPORT_MAX_BYTES - 1) is True
    assert within_import_byte_limit(IMPORT_MAX_BYTES) is True
    assert within_import_byte_limit(IMPORT_MAX_BYTES + 1) is False


def test_import_record_limit_boundaries() -> None:
    assert within_import_record_limit(0) is False
    assert within_import_record_limit(1) is True
    assert within_import_record_limit(IMPORT_MAX_RECORDS - 1) is True
    assert within_import_record_limit(IMPORT_MAX_RECORDS) is True
    assert within_import_record_limit(IMPORT_MAX_RECORDS + 1) is False


def test_revision_alias_is_strict_int() -> None:
    from pydantic import BaseModel

    class Holder(BaseModel):
        revision: Revision

    with pytest.raises(ValidationError):
        Holder(revision=True)
    with pytest.raises(ValidationError):
        Holder(revision=1.0)
