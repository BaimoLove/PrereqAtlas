"""发布查询响应：分页结果同时带出所读版本与内容哈希。"""

from app.common.contracts import IdStr, Page, Sha256Hex
from app.common.http_contracts import GraphNode


class ConceptListResponse(Page[GraphNode]):
    version_id: IdStr
    content_hash: Sha256Hex
