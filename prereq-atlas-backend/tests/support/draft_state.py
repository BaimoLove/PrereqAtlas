"""T09 测试侧草稿可写校验（gate）。

生产的 gate 属于 M06（T10 的 ``graph_admin.service.ensure_draft_writable``）；
T09 只依赖 T02/T03，所以这里用同一语义、真实读取 ``graph_version`` 的测试侧实现，
验证 catalog 在 gate 拒绝时不写入任何行。替身只在 tests 中使用，不参与生产装配。
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.common.contracts import ErrorCode
from app.infra.errors import AppError


def draft_state_gate(session: Session, *, version_id: int, expected_revision: int) -> None:
    """按数据库真实状态判定草稿是否可写；不可写时抛 AppError，不写任何行。"""
    row = session.execute(
        text("SELECT state, draft_revision FROM graph_version WHERE id = :version_id"),
        {"version_id": version_id},
    ).first()
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "图谱版本不存在")
    if row.state != "DRAFT":
        raise AppError(
            ErrorCode.CONFLICT,
            "已发布版本不可编辑",
            {"resource_type": "graph_version", "ids": [str(version_id)], "state": row.state},
        )
    if int(row.draft_revision) != expected_revision:
        raise AppError(
            ErrorCode.CONFLICT,
            "草稿版本号已变化",
            {
                "expected_revision": expected_revision,
                "actual_revision": int(row.draft_revision),
            },
        )
