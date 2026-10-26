"""共享契约的基元：ID、分页、修订号与时间序列化。

这些契约被所有模块共用；测试只断言 HTTP/JSON 层可观察的行为，
不依赖任何持久化实现。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ValidationError

from app.common.contracts import (
    MAX_BIGINT_UNSIGNED,
    MAX_SAFE_INTEGER,
    PAGE_SIZE_MAX,
    IdStr,
    PageParams,
    UtcMillis,
    format_id,
    parse_id,
)
from app.common.contracts import Page as PageEnvelope
from app.common.contracts import ProgressRevision as ProgressRevisionType
from app.common.contracts import Revision as RevisionType


class _TimestampHolder(BaseModel):
    at: UtcMillis


class _IdHolder(BaseModel):
    concept_id: IdStr

    model_config = {"extra": "forbid"}


class _RevisionHolder(BaseModel):
    revision: RevisionType


class _ProgressRevisionHolder(BaseModel):
    revision: ProgressRevisionType


class _PageItem(BaseModel):
    name: str


def test_format_and_parse_id_roundtrip() -> None:
    """十进制字符串与内部 int 必须可逆，且不做 JS Number 隐式转换。"""
    assert format_id(1) == "1"
    assert format_id(MAX_BIGINT_UNSIGNED) == str(MAX_BIGINT_UNSIGNED)
    assert parse_id("42") == 42
    assert parse_id(str(MAX_BIGINT_UNSIGNED)) == MAX_BIGINT_UNSIGNED
    for value in (1, 42, MAX_BIGINT_UNSIGNED):
        assert parse_id(format_id(value)) == value


@pytest.mark.parametrize("raw", ["0", "01", "-1", "+1", "1.0", "1e3", "abc", "", " 1", "1 "])
def test_id_string_rejects_malformed_values(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_id(raw)
    with pytest.raises(ValidationError):
        _IdHolder(concept_id=raw)


def test_id_string_rejects_json_number() -> None:
    """JSON 数字 42 不能被静默接受为 ID 字符串。"""
    with pytest.raises(ValidationError):
        _IdHolder(concept_id=42)  # type: ignore[arg-type]


def test_id_string_rejects_bigint_overflow() -> None:
    overflow = str(MAX_BIGINT_UNSIGNED + 1)
    with pytest.raises(ValueError):
        parse_id(overflow)
    with pytest.raises(ValidationError):
        _IdHolder(concept_id=overflow)


def test_id_holder_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        _IdHolder(concept_id="1", unexpected="x")  # type: ignore[call-arg]


def test_page_params_defaults_and_boundaries() -> None:
    assert PageParams().page == 1
    assert PageParams().page_size == 20
    assert PageParams(page=1, page_size=1).page_size == 1
    assert PageParams(page=1, page_size=PAGE_SIZE_MAX).page_size == PAGE_SIZE_MAX
    for kwargs in ({"page": 0}, {"page": -1}, {"page_size": 0}, {"page_size": PAGE_SIZE_MAX + 1}):
        with pytest.raises(ValidationError):
            PageParams(**kwargs)


def test_page_envelope_serializes_items_and_total() -> None:
    envelope = PageEnvelope[_PageItem](
        items=[_PageItem(name="a"), _PageItem(name="b")], total=2, page=1, page_size=20
    )
    assert envelope.model_dump() == {
        "items": [{"name": "a"}, {"name": "b"}],
        "total": 2,
        "page": 1,
        "page_size": 20,
    }
    with pytest.raises(ValidationError):
        PageEnvelope[_PageItem](items=[], total=-1, page=1, page_size=20)


def test_revision_bounds_are_json_safe_and_ddl_aligned() -> None:
    assert _RevisionHolder(revision=0).revision == 0
    assert _RevisionHolder(revision=MAX_SAFE_INTEGER).revision == MAX_SAFE_INTEGER
    for value in (-1, MAX_SAFE_INTEGER + 1):
        with pytest.raises(ValidationError):
            _RevisionHolder(revision=value)
    assert _ProgressRevisionHolder(revision=2**32 - 1).revision == 2**32 - 1
    for value in (-1, 2**32):
        with pytest.raises(ValidationError):
            _ProgressRevisionHolder(revision=value)


def test_utc_millis_serializes_iso8601_with_z() -> None:
    holder = _TimestampHolder(at=datetime(2026, 10, 26, 13, 18, 15, 123456, tzinfo=UTC))
    assert holder.model_dump_json() == '{"at":"2026-10-26T13:18:15.123Z"}'


def test_utc_millis_treats_naive_input_as_utc() -> None:
    holder = _TimestampHolder(at=datetime(2026, 10, 26, 13, 18, 15, 999999))
    assert holder.model_dump_json() == '{"at":"2026-10-26T13:18:15.999Z"}'
