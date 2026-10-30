"""catalog 的公开用例面；M06 只从此入口调用，不直接导入 catalog 内部实现。

catalog 没有管理写 HTTP 入口：草稿状态由 M06 锁定并通过 ``DraftWriteContext``
传入，客户端无法自行构造可写上下文。
"""

from app.modules.catalog.context import (
    ConceptContent,
    ConceptIdentity,
    CopyContentResult,
    DraftStateGate,
    DraftWriteContext,
    VersionResourceLink,
)
from app.modules.catalog.queries import concept_card, query_graph, search_concepts
from app.modules.catalog.service import (
    add_resource,
    copy_content,
    ensure_concept,
    find_concept_identity,
    read_version_chapters,
    read_version_nodes,
    read_version_resources,
    version_has_concept,
    write_chapter,
    write_concept_content,
)

__all__ = [
    "ConceptContent",
    "ConceptIdentity",
    "CopyContentResult",
    "DraftStateGate",
    "DraftWriteContext",
    "VersionResourceLink",
    "add_resource",
    "concept_card",
    "copy_content",
    "ensure_concept",
    "find_concept_identity",
    "query_graph",
    "read_version_chapters",
    "read_version_nodes",
    "read_version_resources",
    "search_concepts",
    "version_has_concept",
    "write_chapter",
    "write_concept_content",
]
