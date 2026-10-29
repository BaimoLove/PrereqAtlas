"""T09：catalog 稳定身份与草稿内容公开写入面（真实 MySQL，无 HTTP 入口）。

覆盖 plan T09 的 6 项验收：身份复用与命名空间隔离、同版读写一致、跨版引用拒绝、
草稿上下文门禁、版本复制的键隔离、调用方回滚无残留。
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.common.contracts import ErrorCode
from app.infra.errors import AppError
from app.modules.catalog import public as catalog
from app.modules.catalog.context import ConceptContent, DraftWriteContext
from support.draft_state import draft_state_gate

pytestmark = pytest.mark.integration

PUBLISHED_AT = datetime(2026, 10, 27, 10, 0, 0)


@pytest.fixture
def catalog_env(migrated_database) -> tuple[Engine, sessionmaker[Session]]:
    _, engine = migrated_database
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def _context(version_id: int, *, revision: int = 0) -> DraftWriteContext:
    return DraftWriteContext(
        version_id=version_id,
        expected_revision=revision,
        actor_id=1,
        gate=draft_state_gate,
    )


def _create_version(
    engine: Engine,
    *,
    label: str,
    state: str = "DRAFT",
    revision: int = 0,
    base_version_id: int | None = None,
) -> int:
    published = state == "PUBLISHED"
    with engine.begin() as connection:
        result = connection.execute(
            text(
                "INSERT INTO graph_version (version_label, state, base_version_id, "
                "draft_revision, content_hash, created_by, published_by, published_at) "
                "VALUES (:label, :state, :base, :revision, :hash, 1, :published_by, :published_at)"
            ),
            {
                "label": label,
                "state": state,
                "base": base_version_id,
                "revision": revision,
                "hash": ("c" * 64) if published else None,
                "published_by": 1 if published else None,
                "published_at": PUBLISHED_AT if published else None,
            },
        )
        return int(result.lastrowid or 0)


def _publish(engine: Engine, version_id: int) -> str:
    digest = "d" * 64
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE graph_version SET state = 'PUBLISHED', content_hash = :hash, "
                "published_by = 1, published_at = :published_at WHERE id = :version_id"
            ),
            {"hash": digest, "published_at": PUBLISHED_AT, "version_id": version_id},
        )
    return digest


def _scalar(engine: Engine, statement: str, **params: object) -> int:
    with engine.connect() as connection:
        return int(connection.execute(text(statement), params).scalar_one())


def _seed_source_content(engine: Engine, factory: sessionmaker[Session], version_id: int) -> int:
    """在草稿态写入两章两概念两资料，返回其中一个概念 ID。"""
    with factory() as session:
        context = _context(version_id)
        catalog.write_chapter(
            session, context, chapter_code="M01", name="数据挖掘概述", display_order=1
        )
        catalog.write_chapter(
            session, context, chapter_code="M02", name="关联规则", display_order=2
        )
        apriori = catalog.ensure_concept(
            session,
            context,
            code="APRIORI",
            source_namespace="AL-CPL/data_mining",
            source_key="N010",
        )
        knn = catalog.ensure_concept(
            session,
            context,
            code="KNN",
            source_namespace="AL-CPL/data_mining",
            source_key="N011",
        )
        catalog.write_concept_content(
            session,
            context,
            concept_id=apriori,
            content=ConceptContent(
                label_zh="Apriori 算法",
                label_en="Apriori",
                chapter_code="M01",
                node_type="算法",
                introduction="频繁项集挖掘",
                learning_focus="支持度与置信度",
            ),
        )
        catalog.write_concept_content(
            session,
            context,
            concept_id=knn,
            content=ConceptContent(label_zh="K 近邻", chapter_code="M02", node_type="方法"),
        )
        catalog.add_resource(
            session,
            context,
            concept_id=apriori,
            title="教材第 6 章",
            url="https://example.com/apriori",
            source="课程教材",
        )
        catalog.add_resource(
            session,
            context,
            concept_id=knn,
            title="讲义 3",
            url="http://example.com/knn.pdf",
        )
        session.commit()
    return apriori


def test_same_source_namespace_reuses_stable_identity(catalog_env):
    """T09-01：同来源命名空间 + 来源编号始终指向同一个稳定身份。"""
    engine, factory = catalog_env
    version_id = _create_version(engine, label="v-identity")
    context = _context(version_id)
    with factory() as session:
        first = catalog.ensure_concept(
            session,
            context,
            code="KNN",
            source_namespace="AL-CPL/data_mining",
            source_key="N001",
        )
        second = catalog.ensure_concept(
            session,
            context,
            code="KNN",
            source_namespace="AL-CPL/data_mining",
            source_key="N001",
        )
        session.commit()

    assert first == second
    assert _scalar(engine, "SELECT COUNT(*) FROM concept") == 1
    assert _scalar(engine, "SELECT MIN(id) FROM concept") == first


def test_different_namespace_does_not_merge_identities(catalog_env):
    """T09-01：同名来源编号在不同命名空间下是不同概念，且不静默改编码。"""
    engine, factory = catalog_env
    version_id = _create_version(engine, label="v-namespace")
    context = _context(version_id)
    with factory() as session:
        alpha = catalog.ensure_concept(
            session,
            context,
            code="N001_DATA",
            source_namespace="AL-CPL/data_mining",
            source_key="N001",
        )
        beta = catalog.ensure_concept(
            session,
            context,
            code="N001_ML",
            source_namespace="other_course/ml",
            source_key="N001",
        )
        session.commit()
        with pytest.raises(AppError) as conflict:
            catalog.ensure_concept(
                session,
                context,
                code="N001_DATA",
                source_namespace="third_course/dm",
                source_key="N001",
            )
        session.rollback()

    assert alpha != beta
    assert conflict.value.code is ErrorCode.CONFLICT
    assert _scalar(engine, "SELECT COUNT(*) FROM concept") == 2


def test_chapter_concept_resource_round_trip(catalog_env):
    """T09-02：同版章节、概念、资料写入后读取一致，名称去空白且顺序稳定。"""
    engine, factory = catalog_env
    version_id = _create_version(engine, label="v-round-trip")
    context = _context(version_id)
    with factory() as session:
        catalog.write_chapter(
            session, context, chapter_code="M01", name="  数据挖掘概述  ", display_order=2
        )
        catalog.write_chapter(
            session, context, chapter_code="M02", name="关联规则", display_order=1
        )
        concept_id = catalog.ensure_concept(
            session,
            context,
            code="APRIORI",
            source_namespace="AL-CPL/data_mining",
            source_key="N010",
        )
        written = catalog.write_concept_content(
            session,
            context,
            concept_id=concept_id,
            content=ConceptContent(
                label_zh=" Apriori 算法 ",
                label_en="Apriori",
                chapter_code="M01",
                node_type="算法",
                introduction="频繁项集挖掘",
                learning_focus="支持度与置信度",
            ),
        )
        first_resource = catalog.add_resource(
            session,
            context,
            concept_id=concept_id,
            title="教材第 6 章",
            url="https://example.com/apriori",
            source="课程教材",
        )
        catalog.add_resource(
            session,
            context,
            concept_id=concept_id,
            title="讲义 3",
            url="http://example.com/knn.pdf",
        )
        session.commit()

        chapters = catalog.read_version_chapters(session, version_id)
        nodes = catalog.read_version_nodes(session, version_id)

    assert [(c.chapter_code, c.name, c.display_order) for c in chapters] == [
        ("M02", "关联规则", 1),
        ("M01", "数据挖掘概述", 2),
    ]
    assert written.label_zh == "Apriori 算法"
    assert len(nodes) == 1
    node = nodes[0]
    assert node.concept_id == concept_id
    assert node.code == "APRIORI"
    assert node.label_zh == "Apriori 算法"
    assert node.label_en == "Apriori"
    assert node.chapter_code == "M01"
    assert node.node_type == "算法"
    assert node.introduction == "频繁项集挖掘"
    assert [resource.title for resource in node.resources] == ["教材第 6 章", "讲义 3"]
    assert node.resources[0].resource_id == first_resource.resource_id
    assert node.resources[0].url == "https://example.com/apriori"
    assert node.resources[0].source == "课程教材"
    assert node.resources[1].url == "http://example.com/knn.pdf"
    assert _scalar(engine, "SELECT COUNT(*) FROM chapter_revision") == 2
    assert _scalar(engine, "SELECT COUNT(*) FROM concept_revision") == 1
    assert _scalar(engine, "SELECT COUNT(*) FROM resource_revision") == 2


def test_cross_version_chapter_reference_is_rejected_without_writes(catalog_env):
    """T09-03：概念内容引用另一版本的章节时拒绝，且本次写入 0 行。"""
    engine, factory = catalog_env
    source_version = _create_version(engine, label="v-chapter-src")
    target_version = _create_version(engine, label="v-chapter-dst")
    with factory() as session:
        catalog.write_chapter(
            session, _context(source_version), chapter_code="M01", name="来源章节"
        )
        concept_id = catalog.ensure_concept(
            session,
            _context(target_version),
            code="KNN",
            source_namespace="AL-CPL/data_mining",
            source_key="N001",
        )
        session.commit()

    with factory() as session:
        with pytest.raises(AppError) as rejected:
            catalog.write_concept_content(
                session,
                _context(target_version),
                concept_id=concept_id,
                content=ConceptContent(label_zh="K 近邻", chapter_code="M01"),
            )
        session.rollback()

    assert rejected.value.code is ErrorCode.VALIDATION_ERROR
    assert rejected.value.details == {"resource_type": "chapter", "ids": ["M01"]}
    assert _scalar(engine, "SELECT COUNT(*) FROM concept_revision") == 0
    assert _scalar(engine, "SELECT COUNT(*) FROM chapter_revision") == 1


def test_resource_requires_concept_in_same_version(catalog_env):
    """T09-03：资料只能挂在本版本已有的概念版本上。"""
    engine, factory = catalog_env
    source_version = _create_version(engine, label="v-res-src")
    target_version = _create_version(engine, label="v-res-dst")
    with factory() as session:
        context = _context(source_version)
        catalog.write_chapter(session, context, chapter_code="M01", name="章节")
        concept_id = catalog.ensure_concept(
            session, context, code="KNN", source_namespace="AL-CPL/data_mining", source_key="N001"
        )
        catalog.write_concept_content(
            session, context, concept_id=concept_id, content=ConceptContent(label_zh="K 近邻")
        )
        session.commit()

    with factory() as session:
        with pytest.raises(AppError) as rejected:
            catalog.add_resource(
                session,
                _context(target_version),
                concept_id=concept_id,
                title="跨版资料",
                url="https://example.com/x",
            )
        session.rollback()

    assert rejected.value.code is ErrorCode.VALIDATION_ERROR
    assert rejected.value.details == {"resource_type": "concept", "ids": [str(concept_id)]}
    assert _scalar(engine, "SELECT COUNT(*) FROM resource_revision") == 0


def test_write_requires_verified_draft_context(catalog_env):
    """T09-04：缺上下文、已发布版本、旧 revision 三种情况都不写入任何行。"""
    engine, factory = catalog_env
    draft_version = _create_version(engine, label="v-gate-draft")
    published_version = _create_version(engine, label="v-gate-published", state="PUBLISHED")

    with factory() as session:
        with pytest.raises(TypeError):
            catalog.write_chapter(
                session, None, chapter_code="M01", name="无上下文"  # type: ignore[arg-type]
            )
        session.rollback()

        with pytest.raises(AppError) as published:
            catalog.write_chapter(
                session, _context(published_version), chapter_code="M01", name="已发布"
            )
        session.rollback()

        with pytest.raises(AppError) as stale:
            catalog.write_chapter(
                session,
                _context(draft_version, revision=7),
                chapter_code="M01",
                name="旧 revision",
            )
        session.rollback()

    assert published.value.code is ErrorCode.CONFLICT
    assert published.value.details == {
        "resource_type": "graph_version",
        "ids": [str(published_version)],
        "state": "PUBLISHED",
    }
    assert stale.value.code is ErrorCode.CONFLICT
    assert stale.value.details == {"expected_revision": 7, "actual_revision": 0}
    assert _scalar(engine, "SELECT COUNT(*) FROM chapter_revision") == 0


def test_copy_content_uses_new_version_keys_and_keeps_source(catalog_env):
    """T09-05：复制后内容相等但版本键不同，修改副本不影响原版。"""
    engine, factory = catalog_env
    source_version = _create_version(engine, label="v-copy-src")
    target_version = _create_version(engine, label="v-copy-dst")
    _seed_source_content(engine, factory, source_version)
    source_hash = _publish(engine, source_version)

    with factory() as session:
        copied = catalog.copy_content(session, source_version, _context(target_version))
        session.commit()

    assert (copied.chapter_count, copied.concept_count, copied.resource_count) == (2, 2, 2)

    with factory() as session:
        source_nodes = catalog.read_version_nodes(session, source_version)
        target_nodes = catalog.read_version_nodes(session, target_version)
        source_chapters = catalog.read_version_chapters(session, source_version)
        target_chapters = catalog.read_version_chapters(session, target_version)

    assert source_chapters == target_chapters
    assert [node.code for node in target_nodes] == [node.code for node in source_nodes]
    assert [node.concept_id for node in target_nodes] == [
        node.concept_id for node in source_nodes
    ]
    assert [
        [(resource.title, resource.url, resource.source) for resource in node.resources]
        for node in target_nodes
    ] == [
        [(resource.title, resource.url, resource.source) for resource in node.resources]
        for node in source_nodes
    ]
    source_resource_ids = {
        resource.resource_id for node in source_nodes for resource in node.resources
    }
    target_resource_ids = {
        resource.resource_id for node in target_nodes for resource in node.resources
    }
    assert source_resource_ids.isdisjoint(target_resource_ids)
    assert _scalar(engine, "SELECT COUNT(DISTINCT version_id) FROM chapter_revision") == 2
    assert _scalar(engine, "SELECT COUNT(DISTINCT version_id) FROM concept_revision") == 2

    with factory() as session:
        catalog.write_concept_content(
            session,
            _context(target_version),
            concept_id=target_nodes[0].concept_id,
            content=ConceptContent(label_zh="副本改名", chapter_code="M02"),
        )
        session.commit()
    with factory() as session:
        unchanged = catalog.read_version_nodes(session, source_version)

    assert [node.label_zh for node in unchanged] == [node.label_zh for node in source_nodes]
    assert _scalar(
        engine,
        "SELECT COUNT(*) FROM graph_version WHERE id = :version_id AND content_hash = :hash",
        version_id=source_version,
        hash=source_hash,
    ) == 1


def test_caller_rollback_leaves_no_catalog_rows(catalog_env):
    """T09-06：调用方事务回滚后 catalog 四表新增行数为 0。"""
    engine, factory = catalog_env
    version_id = _create_version(engine, label="v-rollback")
    context = _context(version_id)
    with factory() as session:
        catalog.write_chapter(session, context, chapter_code="M01", name="回滚章节")
        concept_id = catalog.ensure_concept(
            session, context, code="KNN", source_namespace="AL-CPL/data_mining", source_key="N001"
        )
        catalog.write_concept_content(
            session,
            context,
            concept_id=concept_id,
            content=ConceptContent(label_zh="K 近邻", chapter_code="M01"),
        )
        catalog.add_resource(
            session, context, concept_id=concept_id, title="资料", url="https://example.com/a"
        )
        session.rollback()

    for table in ("concept", "chapter_revision", "concept_revision", "resource_revision"):
        assert _scalar(engine, f"SELECT COUNT(*) FROM {table}") == 0, table
