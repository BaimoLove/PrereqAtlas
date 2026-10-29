"""按 (version_id, content_hash) 复用已构建的只读快照。

缓存只保存构建成功的不可变快照：失败不写缓存，下一次读取仍会重新校验并失败，
因此非法版本数据不会被“记住”成可服务状态。
"""

from __future__ import annotations

from app.common.contracts import VersionData
from app.modules.graph_runtime.builder import GraphSnapshot, build_snapshot

SnapshotKey = tuple[int, str | None]


class SnapshotCache:
    """进程内快照缓存；键是版本与内容哈希的组合。"""

    def __init__(self) -> None:
        self._snapshots: dict[SnapshotKey, GraphSnapshot] = {}

    def get_or_build(self, data: VersionData) -> GraphSnapshot:
        key = (data.version_id, data.content_hash)
        cached = self._snapshots.get(key)
        if cached is not None:
            return cached
        snapshot = build_snapshot(data)
        self._snapshots[key] = snapshot
        return snapshot

    def __len__(self) -> int:
        return len(self._snapshots)
