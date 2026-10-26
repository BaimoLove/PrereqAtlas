"""HTTP 请求/响应契约：字段、枚举、额外字段拒绝与序列化。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.common.http_contracts import (
    AcceptImportRequest,
    AdminRelation,
    AuditItem,
    AuditListResponse,
    ChapterDeleteRequest,
    ChapterItem,
    ChapterSaveRequest,
    ConceptCard,
    ConceptContentOut,
    ConceptCreateRequest,
    ConceptDeleteRequest,
    ConceptDetailQuery,
    ConceptMutationResponse,
    ConceptSearchQuery,
    ConceptUpdateRequest,
    CreateDraftRequest,
    DraftCreateResponse,
    DraftDetail,
    DraftReviewSummary,
    DraftValidationSummary,
    GraphEdge,
    GraphNode,
    GraphResponse,
    HealthLiveResponse,
    HealthReadyResponse,
    ImportIssue,
    ImportReport,
    MasteryItem,
    MasteryListResponse,
    PageQuery,
    PlanCreateRequest,
    PlanDetail,
    PlanListItem,
    PlanListResponse,
    ProgressItem,
    ProgressUpdateRequest,
    ProgressUpdateResponse,
    PublishDraftRequest,
    PublishResponse,
    RegisterRequest,
    RelationBatchReviewRequest,
    RelationCreateRequest,
    RelationDeleteRequest,
    RelationMutationResponse,
    RelationUpdateRequest,
    ReplanRequest,
    ResourceCreateRequest,
    ResourceDeleteRequest,
    ResourceItem,
    ResourceMutationResponse,
    ResourceUpdateRequest,
    RevisionResponse,
    TokenForm,
    UserSummary,
    ValidateDraftRequest,
    ValidationIssue,
    ValidationReport,
    VersionListQuery,
    VersionListResponse,
    VersionSummary,
)

REQUEST_ID = "7f6f0a52-0d5c-4a63-9a4c-2a8d1f0e5b11"

VALID_REQUEST_PAYLOADS: dict[str, dict[str, object]] = {
    "RegisterRequest": {"username": "student_1", "password": "password123"},
    "TokenForm": {"username": "student_1", "password": "password123"},
    "CreateDraftRequest": {"version_label": "v1.0"},
    "AcceptImportRequest": {"batch_id": "1", "expected_revision": 0},
    "ChapterSaveRequest": {"name": "统计基础", "expected_revision": 0},
    "ChapterDeleteRequest": {"expected_revision": 0},
    "ConceptCreateRequest": {"code": "N001", "label_zh": "算术平均数", "expected_revision": 0},
    "ConceptUpdateRequest": {"label_zh": "算术平均数", "expected_revision": 0},
    "ConceptDeleteRequest": {"expected_revision": 0},
    "ResourceCreateRequest": {
        "concept_id": "1",
        "title": "维基百科",
        "url": "https://example.com/a",
        "expected_revision": 0,
    },
    "ResourceUpdateRequest": {
        "concept_id": "1",
        "title": "维基百科",
        "url": "https://example.com/a",
        "expected_revision": 0,
    },
    "ResourceDeleteRequest": {"expected_revision": 0},
    "RelationCreateRequest": {
        "source_concept_id": "1",
        "target_concept_id": "2",
        "expected_revision": 0,
    },
    "RelationUpdateRequest": {"expected_revision": 0, "source_concept_id": "3"},
    "RelationBatchReviewRequest": {
        "relation_ids": ["1"],
        "review_status": "APPROVED",
        "rationale": "人工核验",
        "expected_revision": 0,
    },
    "RelationDeleteRequest": {"expected_revision": 0},
    "ValidateDraftRequest": {"expected_revision": 0},
    "PublishDraftRequest": {"expected_revision": 0, "expected_active_revision": 0},
    "PlanCreateRequest": {"target_ids": ["3"], "request_id": REQUEST_ID},
    "ReplanRequest": {"request_id": REQUEST_ID},
    "ProgressUpdateRequest": {"status": "IN_PROGRESS", "expected_revision": 0},
}

REQUEST_MODELS = {
    "RegisterRequest": RegisterRequest,
    "TokenForm": TokenForm,
    "CreateDraftRequest": CreateDraftRequest,
    "AcceptImportRequest": AcceptImportRequest,
    "ChapterSaveRequest": ChapterSaveRequest,
    "ChapterDeleteRequest": ChapterDeleteRequest,
    "ConceptCreateRequest": ConceptCreateRequest,
    "ConceptUpdateRequest": ConceptUpdateRequest,
    "ConceptDeleteRequest": ConceptDeleteRequest,
    "ResourceCreateRequest": ResourceCreateRequest,
    "ResourceUpdateRequest": ResourceUpdateRequest,
    "ResourceDeleteRequest": ResourceDeleteRequest,
    "RelationCreateRequest": RelationCreateRequest,
    "RelationUpdateRequest": RelationUpdateRequest,
    "RelationBatchReviewRequest": RelationBatchReviewRequest,
    "RelationDeleteRequest": RelationDeleteRequest,
    "ValidateDraftRequest": ValidateDraftRequest,
    "PublishDraftRequest": PublishDraftRequest,
    "PlanCreateRequest": PlanCreateRequest,
    "ReplanRequest": ReplanRequest,
    "ProgressUpdateRequest": ProgressUpdateRequest,
}


@pytest.mark.parametrize("name", sorted(REQUEST_MODELS))
def test_request_models_accept_valid_payload(name: str) -> None:
    model = REQUEST_MODELS[name]
    payload = VALID_REQUEST_PAYLOADS[name]
    instance = model.model_validate(payload)
    assert instance.model_dump() is not None


@pytest.mark.parametrize("name", sorted(REQUEST_MODELS))
def test_request_models_reject_extra_fields(name: str) -> None:
    model = REQUEST_MODELS[name]
    payload = dict(VALID_REQUEST_PAYLOADS[name])
    payload["owner_id"] = "9"
    payload["role"] = "ADMIN"
    payload["mastered_concept_ids"] = ["1"]
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_register_username_and_password_rules() -> None:
    assert RegisterRequest(username="abc", password="12345678").username == "abc"
    assert RegisterRequest(username="A" * 32, password="p" * 128).password == "p" * 128
    for username in ("ab", "A" * 33, "a b", "用户", "a-b", "a.b", ""):
        with pytest.raises(ValidationError):
            RegisterRequest(username=username, password="12345678")
    for password in ("1234567", "p" * 129, ""):
        with pytest.raises(ValidationError):
            RegisterRequest(username="abc", password=password)
    # 密码不做 TRIM：首尾空格是密码的一部分。
    assert RegisterRequest(username="abc", password=" 12345678 ").password == " 12345678 "


def test_plan_title_is_trimmed_and_blank_is_rejected() -> None:
    plan = PlanCreateRequest(target_ids=["1"], request_id=REQUEST_ID, title="  复习计划  ")
    assert plan.title == "复习计划"
    assert PlanCreateRequest(target_ids=["1"], request_id=REQUEST_ID).title is None
    for title in ("", "   ", "x" * 101):
        with pytest.raises(ValidationError):
            PlanCreateRequest(target_ids=["1"], request_id=REQUEST_ID, title=title)


def test_plan_request_id_must_be_uuid() -> None:
    plan = PlanCreateRequest(target_ids=["1"], request_id=REQUEST_ID)
    assert plan.request_id == UUID(REQUEST_ID)
    for raw in ("not-a-uuid", "1234", ""):
        with pytest.raises(ValidationError):
            PlanCreateRequest(target_ids=["1"], request_id=raw)


def test_target_ids_raw_count_boundaries_and_duplicates() -> None:
    assert PlanCreateRequest(target_ids=["1"], request_id=REQUEST_ID).target_ids == ["1"]
    assert len(PlanCreateRequest(target_ids=["1"] * 20, request_id=REQUEST_ID).target_ids) == 20
    for size in (0, 21):
        with pytest.raises(ValidationError):
            PlanCreateRequest(target_ids=["1"] * size, request_id=REQUEST_ID)


def test_batch_review_boundaries_and_decision() -> None:
    assert len(
        RelationBatchReviewRequest(
            relation_ids=["1"] * 112,
            review_status="APPROVED",
            rationale="核验",
            expected_revision=0,
        ).relation_ids
    ) == 112
    for size in (0, 113):
        with pytest.raises(ValidationError):
            RelationBatchReviewRequest(
                relation_ids=["1"] * size,
                review_status="APPROVED",
                rationale="核验",
                expected_revision=0,
            )
    for decision in ("PENDING", "APPROVED ", "approved"):
        with pytest.raises(ValidationError):
            RelationBatchReviewRequest(
                relation_ids=["1"],
                review_status=decision,
                rationale="核验",
                expected_revision=0,
            )
    for rationale in ("", "   ", "x" * 501):
        with pytest.raises(ValidationError):
            RelationBatchReviewRequest(
                relation_ids=["1"],
                review_status="REJECTED",
                rationale=rationale,
                expected_revision=0,
            )


def test_relation_create_fixes_necessity_and_hides_review_fields() -> None:
    relation = RelationCreateRequest(
        source_concept_id="1", target_concept_id="2", expected_revision=0
    )
    assert relation.necessity == "REQUIRED"
    for necessity in ("RECOMMENDED", "OPTIONAL", "required"):
        with pytest.raises(ValidationError):
            RelationCreateRequest(
                source_concept_id="1",
                target_concept_id="2",
                necessity=necessity,
                expected_revision=0,
            )
    with pytest.raises(ValidationError):
        RelationCreateRequest(
            source_concept_id="1",
            target_concept_id="2",
            review_status="APPROVED",
            expected_revision=0,
        )


def test_relation_update_field_combinations() -> None:
    with pytest.raises(ValidationError):
        RelationUpdateRequest(expected_revision=0)
    with pytest.raises(ValidationError):
        RelationUpdateRequest(expected_revision=0, source_concept_id="1", target_concept_id="1")
    with pytest.raises(ValidationError):
        RelationUpdateRequest(
            expected_revision=0, review_status="APPROVED", rationale="核验", source_concept_id="3"
        )
    with pytest.raises(ValidationError):
        RelationUpdateRequest(expected_revision=0, review_status="APPROVED")
    with pytest.raises(ValidationError):
        RelationUpdateRequest(
            expected_revision=0, review_status="PENDING", rationale="核验"
        )
    approved = RelationUpdateRequest(
        expected_revision=0, review_status="APPROVED", rationale="纳入理由"
    )
    assert approved.review_status is not None
    endpoint = RelationUpdateRequest(expected_revision=0, target_concept_id="9")
    assert endpoint.target_concept_id == "9"


def test_concept_source_pair_must_be_both_or_neither() -> None:
    ConceptCreateRequest(
        code="N002",
        source_namespace="AL-CPL/data_mining",
        source_key="N002",
        label_zh="描述性统计",
        expected_revision=0,
    )
    with pytest.raises(ValidationError):
        ConceptCreateRequest(
            code="N002", source_namespace="AL-CPL", label_zh="描述性统计", expected_revision=0
        )
    with pytest.raises(ValidationError):
        ConceptCreateRequest(
            code="N002", source_key="N002", label_zh="描述性统计", expected_revision=0
        )


def test_code_fields_reject_whitespace_control_and_paths() -> None:
    for code in ("N 001", "N/001", "N\\001", "../N001", "..", ".", "N\x00", " N001", "N001 "):
        with pytest.raises(ValidationError):
            ConceptCreateRequest(code=code, label_zh="概念", expected_revision=0)
    created = ConceptCreateRequest(
        code="DM-001_v2", label_zh="概念", chapter_code="M01", expected_revision=0
    )
    assert created.code == "DM-001_v2"


def test_chapter_code_max_length_and_charset() -> None:
    assert ChapterSaveRequest(name="x", expected_revision=0).display_order == 0
    assert ChapterSaveRequest(name="x", display_order=2**32 - 1, expected_revision=0).display_order
    with pytest.raises(ValidationError):
        ChapterSaveRequest(name="x", display_order=-1, expected_revision=0)


def test_resource_url_accepts_only_http_and_https() -> None:
    ResourceCreateRequest(
        concept_id="1", title="t", url="http://example.com/a", expected_revision=0
    )
    ResourceCreateRequest(
        concept_id="1", title="t", url="https://example.com/a?q=1#f", expected_revision=0
    )
    for url in (
        "javascript:alert(1)",
        "file:///C:/x",
        "data:text/plain,x",
        "ftp://example.com/a",
        "//example.com/a",
        "/relative",
        "https:///nohost",
        " https://example.com",
        "https://example.com/" + "a" * 1000,
    ):
        with pytest.raises(ValidationError):
            ResourceCreateRequest(concept_id="1", title="t", url=url, expected_revision=0)


def test_query_models_validate_pagination_and_search() -> None:
    query = ConceptSearchQuery(version_id="1")
    assert (query.page, query.page_size, query.q, query.chapter) == (1, 20, None, None)
    ConceptSearchQuery(version_id="1", q="x" * 100, chapter="M01")
    for kwargs in ({"page": 0}, {"page_size": 0}, {"page_size": 101}, {"q": "x" * 101}):
        with pytest.raises(ValidationError):
            ConceptSearchQuery(version_id="1", **kwargs)
    with pytest.raises(ValidationError):
        ConceptDetailQuery()
    assert PageQuery().page == 1
    assert VersionListQuery(state="DRAFT").state is not None
    with pytest.raises(ValidationError):
        VersionListQuery(state="ARCHIVED")


def test_user_summary_and_token_response_shapes() -> None:
    user = UserSummary(id="42", username="student_1", role="STUDENT")
    assert user.model_dump() == {"id": "42", "username": "student_1", "role": "STUDENT"}
    with pytest.raises(ValidationError):
        UserSummary(id=42, username="student_1", role="STUDENT")  # type: ignore[arg-type]


def test_plan_detail_serializes_ids_as_strings_and_timestamps_as_utc_millis() -> None:
    detail = PlanDetail(
        id="42",
        title="复习方案",
        owner_id="7",
        graph_version_id="3",
        schema_version="1",
        algorithm_version="1.0",
        created_at=datetime(2026, 10, 26, 13, 18, 15, 123456, tzinfo=UTC),
        input_snapshot={"target_ids": ["5"]},
        result_snapshot={"stages": []},
        progress=[
            ProgressItem(
                concept_id="5",
                stage_index=1,
                status="NOT_STARTED",
                revision=0,
                updated_at=datetime(2026, 10, 26, 13, 18, 15, 999999, tzinfo=UTC),
            )
        ],
    )
    payload = detail.model_dump(mode="json")
    assert payload["id"] == "42"
    assert payload["owner_id"] == "7"
    assert payload["created_at"] == "2026-10-26T13:18:15.123Z"
    assert payload["progress"][0]["updated_at"] == "2026-10-26T13:18:15.999Z"
    assert payload["progress"][0]["concept_id"] == "5"


def test_graph_response_uses_nested_content_and_adopted_edges_only() -> None:
    content = ConceptContentOut(label_zh="算术平均数", chapter_code="M01")
    graph = GraphResponse(
        version_id="3",
        content_hash="a" * 64,
        chapters=[ChapterItem(chapter_code="M01", name="统计基础", display_order=1)],
        nodes=[GraphNode(concept_id="1", code="N001", content=content)],
        edges=[
            GraphEdge(
                relation_id="9", source_id="1", target_id="2", necessity="REQUIRED"
            )
        ],
    )
    payload = graph.model_dump(mode="json")
    assert payload["nodes"][0]["content"]["label_zh"] == "算术平均数"
    assert payload["edges"][0]["source_id"] == "1"
    assert payload["content_hash"] == "a" * 64
    with pytest.raises(ValidationError):
        GraphResponse(
            version_id="3",
            content_hash="short",
            chapters=[],
            nodes=[],
            edges=[],
        )


def test_concept_card_relations_and_resources() -> None:
    card = ConceptCard(
        version_id="3",
        concept_id="1",
        code="N001",
        content=ConceptContentOut(label_zh="算术平均数"),
        resources=[ResourceItem(resource_id="5", title="资料", url="https://example.com")],
        prerequisites=[],
        successors=[],
    )
    payload = card.model_dump(mode="json")
    assert payload["resources"][0]["resource_id"] == "5"
    assert payload["prerequisites"] == []


def test_mastery_list_and_visibility() -> None:
    item = MasteryItem(
        concept_id="1",
        declared_at=datetime(2026, 10, 26, tzinfo=UTC),
        declared_version_id="3",
        visibility="UNKNOWN",
    )
    listing = MasteryListResponse(items=[item], total=1, page=1, page_size=20)
    payload = listing.model_dump(mode="json")
    assert payload["items"][0]["visibility"] == "UNKNOWN"
    assert payload["items"][0]["declared_version_id"] == "3"
    with pytest.raises(ValidationError):
        MasteryItem(
            concept_id="1",
            declared_at=datetime(2026, 10, 26, tzinfo=UTC),
            declared_version_id="3",
            visibility="MASTERED",
        )


def test_import_report_and_validation_report_shapes() -> None:
    issue = ImportIssue(
        file_kind="node",
        logical_record_no=3,
        source_key="N003",
        code="DUPLICATE_ID",
        message="重复节点编号",
    )
    report = ImportReport(
        batch_id="8",
        status="STAGED",
        node_sha256="b" * 64,
        edge_sha256="c" * 64,
        counts={"nodes": 120, "edges": 292},
        errors=[issue],
        warnings=[],
        mapping_summary={"mapped": 120},
    )
    assert report.model_dump(mode="json")["errors"][0]["logical_record_no"] == 3

    validation = ValidationReport(
        draft_id="4",
        revision=7,
        valid=False,
        issues=[
            ValidationIssue(
                code="CYCLE",
                concept_ids=["1", "2"],
                relation_ids=["9", "10"],
                message="检测到环",
            )
        ],
        approved_count=1,
        rejected_count=0,
        pending_count=2,
        cycle_paths=[["1", "2", "1"]],
    )
    payload = validation.model_dump(mode="json")
    assert payload["cycle_paths"] == [["1", "2", "1"]]
    assert payload["issues"][0]["relation_ids"] == ["9", "10"]


def test_draft_detail_and_mutation_responses() -> None:
    relation = AdminRelation(
        id="9",
        source_concept_id="1",
        target_concept_id="2",
        necessity="REQUIRED",
        review_status="APPROVED",
        rationale="人工核验",
        reviewed_by="7",
        reviewed_at=datetime(2026, 10, 26, tzinfo=UTC),
    )
    detail = DraftDetail(
        draft_id="4",
        version_label="v1.0",
        state="DRAFT",
        draft_revision=3,
        chapters=[],
        concepts=[],
        relations=[relation],
        resources=[],
        review_summary=DraftReviewSummary(approved_count=1, rejected_count=0, pending_count=0),
        validation_summary=DraftValidationSummary(
            validated_revision=None, valid=False, issue_count=0
        ),
    )
    assert detail.model_dump(mode="json")["relations"][0]["reviewed_by"] == "7"

    assert RelationMutationResponse(
        relation=relation, new_revision=4
    ).model_dump(mode="json")["new_revision"] == 4
    assert (
        ConceptMutationResponse(
            concept=GraphNode(
                concept_id="1", code="N001", content=ConceptContentOut(label_zh="概念")
            ),
            new_revision=5,
        ).model_dump(mode="json")["concept"]["content"]["label_zh"]
        == "概念"
    )
    assert (
        ResourceMutationResponse(
            resource=ResourceItem(resource_id="1", title="t", url="https://e.com"),
            new_revision=6,
        ).new_revision
        == 6
    )
    assert RevisionResponse(new_revision=7).new_revision == 7
    assert DraftCreateResponse(draft_id="4", revision=0).draft_id == "4"


def test_publish_progress_health_and_audit_shapes() -> None:
    assert PublishResponse(
        version_id="3", published=True, runtime_ready=False, active_revision=1
    ).model_dump() == {
        "version_id": "3",
        "published": True,
        "runtime_ready": False,
        "active_revision": 1,
    }
    assert ProgressUpdateResponse(status="COMPLETED", new_revision=2).new_revision == 2
    assert HealthLiveResponse().status == "ok"
    assert HealthReadyResponse(database="ok").status == "ready"
    audit = AuditItem(
        version_id="3",
        operator_id="7",
        action="PUBLISH",
        revision_before=2,
        revision_after=3,
        summary="发布 v1.0",
        created_at=datetime(2026, 10, 26, tzinfo=UTC),
    )
    listing = AuditListResponse(items=[audit], total=1, page=1, page_size=20)
    assert listing.model_dump(mode="json")["items"][0]["action"] == "PUBLISH"


def test_version_summary_and_plan_list_item() -> None:
    summary = VersionSummary(
        version_id="3",
        version_label="v1.0",
        state="PUBLISHED",
        content_hash="d" * 64,
        draft_revision=2,
        validated_revision=2,
        published_at=datetime(2026, 10, 26, tzinfo=UTC),
        is_active=True,
        active_revision=1,
    )
    assert summary.is_active is True
    assert VersionListResponse(items=[summary], total=1, page=1, page_size=20).total == 1
    item = PlanListItem(
        id="42",
        title="复习方案",
        graph_version_id="3",
        created_at=datetime(2026, 10, 26, tzinfo=UTC),
        target_count=1,
        node_count=2,
        completed_count=1,
    )
    assert PlanListResponse(items=[item], total=1, page=1, page_size=20).items[0].id == "42"


def test_progress_update_status_enum() -> None:
    for status in ("NOT_STARTED", "IN_PROGRESS", "COMPLETED"):
        assert ProgressUpdateRequest(status=status, expected_revision=0).status == status
    with pytest.raises(ValidationError):
        ProgressUpdateRequest(status="DONE", expected_revision=0)
