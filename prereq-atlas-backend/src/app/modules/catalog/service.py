"""M02 知识目录的草稿内容用例。

职责边界：本模块只写自有四表（concept、chapter_revision、concept_revision、
resource_revision），不碰 graph_version，也不提交事务——事务由调用方（M06）组织。
同一版本内的引用（章节、概念、资料）在写入前校验，不依赖数据库外键。
"""

from __future__ import annotations

from typing import NoReturn
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.common.contracts import (
    CHAPTER_CODE_MAX,
    CHAPTER_NAME_MAX,
    CONCEPT_CODE_MAX,
    LABEL_EN_MAX,
    LABEL_ZH_MAX,
    NODE_TYPE_MAX,
    SOURCE_KEY_MAX,
    SOURCE_NAMESPACE_MAX,
    TEXT_MAX,
    URL_MAX,
    ErrorCode,
    VersionChapter,
    VersionNode,
    VersionResource,
)
from app.infra.errors import AppError
from app.modules.catalog import repository
from app.modules.catalog.context import (
    ConceptContent,
    CopyContentResult,
    DraftWriteContext,
    VersionResourceLink,
)

MAX_DISPLAY_ORDER = 2**32 - 1


def _field_error(field: str, reason: str) -> NoReturn:
    raise AppError(
        ErrorCode.VALIDATION_ERROR,
        "草稿内容不合法",
        {"errors": [{"field": field, "reason": reason}]},
    )


def _reference_error(resource_type: str, ids: list[str], message: str) -> NoReturn:
    raise AppError(
        ErrorCode.VALIDATION_ERROR,
        message,
        {"resource_type": resource_type, "ids": ids},
    )


def _require_context(context: DraftWriteContext | None, tx: Session) -> DraftWriteContext:
    """没有上下文或上下文不可写时立即失败，调用方一行也写不进去。"""
    if not isinstance(context, DraftWriteContext):
        raise TypeError("catalog 写入必须提供 DraftWriteContext")
    context.ensure_writable(tx)
    return context


def _require_text(value: str, field: str, *, max_length: int) -> str:
    text = value.strip()
    if not text:
        _field_error(field, "不能为空")
    if len(text) > max_length:
        _field_error(field, f"长度不能超过 {max_length} 个字符")
    return text


def _optional_text(value: str | None, field: str, *, max_length: int) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) > max_length:
        _field_error(field, f"长度不能超过 {max_length} 个字符")
    return text


def _require_code(value: str, field: str, *, max_length: int) -> str:
    if not value or len(value) > max_length:
        _field_error(field, f"长度必须在 1～{max_length} 个字符之间")
    if value != value.strip() or any(char.isspace() for char in value):
        _field_error(field, "不能包含空白")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        _field_error(field, "不能包含控制字符")
    if "/" in value or "\\" in value or value in {".", ".."}:
        _field_error(field, "不能包含路径片段")
    return value


def _require_namespace(value: str, field: str) -> str:
    if not value or len(value) > SOURCE_NAMESPACE_MAX:
        _field_error(field, f"长度必须在 1～{SOURCE_NAMESPACE_MAX} 个字符之间")
    if value != value.strip() or any(char.isspace() for char in value):
        _field_error(field, "不能包含空白")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        _field_error(field, "不能包含控制字符")
    if "\\" in value or value in {".", ".."} or ".." in value.split("/"):
        _field_error(field, "不能包含路径片段")
    return value


def _require_http_url(value: str, field: str) -> str:
    if len(value) > URL_MAX:
        _field_error(field, f"长度不能超过 {URL_MAX} 个字符")
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        _field_error(field, "不能包含空白或控制字符")
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        _field_error(field, "仅允许带主机名的 http/https 链接")
    return value


def write_chapter(
    tx: Session,
    context: DraftWriteContext,
    *,
    chapter_code: str,
    name: str,
    display_order: int = 0,
) -> VersionChapter:
    """在本草稿版本内新增或更新一个章节。"""
    _require_context(context, tx)
    code = _require_code(chapter_code, "chapter_code", max_length=CHAPTER_CODE_MAX)
    label = _require_text(name, "name", max_length=CHAPTER_NAME_MAX)
    if display_order < 0 or display_order > MAX_DISPLAY_ORDER:
        _field_error("display_order", f"必须在 0～{MAX_DISPLAY_ORDER} 之间")
    row = repository.upsert_chapter(
        tx,
        version_id=context.version_id,
        chapter_code=code,
        name=label,
        display_order=display_order,
    )
    return VersionChapter(
        chapter_code=row.chapter_code, name=row.name, display_order=row.display_order
    )


def ensure_concept(
    tx: Session,
    context: DraftWriteContext,
    *,
    code: str,
    source_namespace: str | None = None,
    source_key: str | None = None,
) -> int:
    """取得概念的稳定身份：来源身份优先，其次系统编码；不会静默改编码或合并。"""
    _require_context(context, tx)
    concept_code = _require_code(code, "code", max_length=CONCEPT_CODE_MAX)
    if (source_namespace is None) != (source_key is None):
        _field_error("source_namespace", "source_namespace 与 source_key 必须同时提供或同时缺省")
    namespace: str | None = None
    key: str | None = None
    if source_namespace is not None and source_key is not None:
        namespace = _require_namespace(source_namespace, "source_namespace")
        key = _require_code(source_key, "source_key", max_length=SOURCE_KEY_MAX)

    if namespace is not None and key is not None:
        existing = repository.find_concept_by_source(tx, namespace, key)
        if existing is not None:
            if existing.code != concept_code:
                raise AppError(
                    ErrorCode.CONFLICT,
                    "来源身份已对应其他概念编码",
                    {
                        "resource_type": "concept",
                        "ids": [str(existing.id)],
                        "code": concept_code,
                        "existing_code": existing.code,
                    },
                )
            return existing.id

    by_code = repository.find_concept_by_code(tx, concept_code)
    if by_code is not None:
        if namespace is not None and by_code.source_namespace not in (None, namespace):
            raise AppError(
                ErrorCode.CONFLICT,
                "概念编码已属于其他来源命名空间",
                {
                    "resource_type": "concept",
                    "ids": [str(by_code.id)],
                    "code": concept_code,
                    "existing_namespace": by_code.source_namespace,
                },
            )
        return by_code.id

    created = repository.create_concept(
        tx, code=concept_code, source_namespace=namespace, source_key=key
    )
    return created.id


def write_concept_content(
    tx: Session,
    context: DraftWriteContext,
    *,
    concept_id: int,
    content: ConceptContent,
) -> VersionNode:
    """写入概念在本版本中的显示内容；引用章节必须属于同一版本。"""
    _require_context(context, tx)
    label_zh = _require_text(content.label_zh, "label_zh", max_length=LABEL_ZH_MAX)
    label_en = _optional_text(content.label_en, "label_en", max_length=LABEL_EN_MAX)
    node_type = _optional_text(content.node_type, "node_type", max_length=NODE_TYPE_MAX)
    introduction = _optional_text(content.introduction, "introduction", max_length=TEXT_MAX)
    learning_focus = _optional_text(
        content.learning_focus, "learning_focus", max_length=TEXT_MAX
    )
    chapter_code: str | None = None
    if content.chapter_code is not None:
        chapter_code = _require_code(
            content.chapter_code, "chapter_code", max_length=CHAPTER_CODE_MAX
        )

    if repository.get_concept(tx, concept_id) is None:
        _reference_error("concept", [str(concept_id)], "概念稳定身份不存在")
    if chapter_code is not None and (
        repository.get_chapter(tx, context.version_id, chapter_code) is None
    ):
        _reference_error("chapter", [chapter_code], "章节不属于当前草稿版本")

    repository.upsert_concept_revision(
        tx,
        version_id=context.version_id,
        concept_id=concept_id,
        label_zh=label_zh,
        label_en=label_en,
        chapter_code=chapter_code,
        node_type=node_type,
        introduction=introduction,
        learning_focus=learning_focus,
    )
    code = repository.get_concept_code(tx, concept_id) or ""
    resources = tuple(
        VersionResource(
            resource_id=row.resource_id,
            title=row.title,
            url=row.url,
            source=row.source,
        )
        for row in repository.list_resources(tx, context.version_id, concept_id=concept_id)
    )
    return VersionNode(
        concept_id=concept_id,
        code=code,
        label_zh=label_zh,
        label_en=label_en,
        chapter_code=chapter_code,
        node_type=node_type,
        introduction=introduction,
        learning_focus=learning_focus,
        resources=resources,
    )


def add_resource(
    tx: Session,
    context: DraftWriteContext,
    *,
    concept_id: int,
    title: str,
    url: str,
    source: str | None = None,
) -> VersionResource:
    """给本草稿版本内已有的概念版本追加一条资料。"""
    _require_context(context, tx)
    resource_title = _require_text(title, "title", max_length=200)
    resource_url = _require_http_url(url, "url")
    resource_source = _optional_text(source, "source", max_length=200)
    if repository.get_concept_revision(tx, context.version_id, concept_id) is None:
        _reference_error("concept", [str(concept_id)], "概念不属于当前草稿版本")
    row = repository.add_resource(
        tx,
        version_id=context.version_id,
        concept_id=concept_id,
        title=resource_title,
        url=resource_url,
        source=resource_source,
    )
    return VersionResource(
        resource_id=row.resource_id, title=row.title, url=row.url, source=row.source
    )


def read_version_chapters(tx: Session, version_id: int) -> tuple[VersionChapter, ...]:
    return tuple(
        VersionChapter(
            chapter_code=row.chapter_code, name=row.name, display_order=row.display_order
        )
        for row in repository.list_chapters(tx, version_id)
    )


def read_version_nodes(tx: Session, version_id: int) -> tuple[VersionNode, ...]:
    return tuple(repository.list_nodes(tx, version_id))


def read_version_resources(tx: Session, version_id: int) -> tuple[VersionResourceLink, ...]:
    return tuple(
        VersionResourceLink(concept_id=concept_id, resource=resource)
        for concept_id, resource in repository.list_resource_links(tx, version_id)
    )


def copy_content(
    tx: Session, source_version_id: int, target: DraftWriteContext
) -> CopyContentResult:
    """把来源版本的四类内容复制到目标草稿；资料在新版本生成新行。"""
    _require_context(target, tx)
    chapters = repository.list_chapters(tx, source_version_id)
    nodes = repository.list_nodes(tx, source_version_id)
    for chapter in chapters:
        write_chapter(
            tx,
            target,
            chapter_code=chapter.chapter_code,
            name=chapter.name,
            display_order=chapter.display_order,
        )
    resource_count = 0
    for node in nodes:
        write_concept_content(
            tx,
            target,
            concept_id=node.concept_id,
            content=ConceptContent(
                label_zh=node.label_zh,
                label_en=node.label_en,
                chapter_code=node.chapter_code,
                node_type=node.node_type,
                introduction=node.introduction,
                learning_focus=node.learning_focus,
            ),
        )
        for resource in node.resources:
            add_resource(
                tx,
                target,
                concept_id=node.concept_id,
                title=resource.title,
                url=resource.url,
                source=resource.source,
            )
            resource_count += 1
    return CopyContentResult(
        chapter_count=len(chapters),
        concept_count=len(nodes),
        resource_count=resource_count,
    )
