"""M05 复习方案与进度的持久化模型。

对应 ``database/schema.sql`` 的 ``review_plan`` 与 ``review_plan_node``。方案固定
输入与结果快照，进度只写在节点上；后续图谱或掌握状态变化不改写历史方案。
模型不声明 ``relationship``，节点与方案的归属、同版本约束由服务层校验。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Index, String, UniqueConstraint, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base


class ReviewPlan(Base):
    """复习方案数据对象（DO），对应 ``review_plan`` 表。

    ``(owner_id, request_id)`` 唯一支持幂等重试；``input_snapshot`` 与
    ``result_snapshot`` 保存当时的输入和结果，读历史时不重新计算。
    """

    __tablename__ = "review_plan"
    __table_args__ = (
        UniqueConstraint("owner_id", "request_id", name="uk_review_plan_request"),
        Index("idx_review_plan_history", "owner_id", "created_at", "id"),
        Index("idx_review_plan_version", "graph_version_id"),
        Index("idx_review_plan_based_on", "based_on_plan_id"),
        CheckConstraint("CHAR_LENGTH(TRIM(title)) BETWEEN 1 AND 120", name="ck_review_plan_title"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "M05 复习方案：固定输入、结果快照与幂等键",
        },
    )

    id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), primary_key=True, autoincrement=True, comment="方案主键"
    )
    owner_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=False, comment="方案所属学生；逻辑引用 app_user.id"
    )
    graph_version_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        nullable=False,
        comment="规划时固定的图谱版本；逻辑引用 graph_version.id",
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False, comment="方案标题")
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="客户端幂等请求键")
    request_hash: Mapped[str] = mapped_column(
        mysql.CHAR(64, charset="ascii", collation="ascii_bin"),
        nullable=False,
        comment="规范化输入 SHA-256，用于同键异输入检测",
    )
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, comment="快照结构版本")
    algorithm_version: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="规划规则版本"
    )
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, comment="输入快照：目标、声明集合、版本与规则版本"
    )
    result_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, comment="结果快照：阶段、节点、解释、边界与结论"
    )
    based_on_plan_id: Mapped[int | None] = mapped_column(
        mysql.BIGINT(unsigned=True),
        nullable=True,
        comment="重规划所依据的旧方案；逻辑引用 review_plan.id",
    )
    created_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
        comment="创建时间",
    )


class ReviewPlanNode(Base):
    """方案节点数据对象（DO），对应 ``review_plan_node`` 表。

    只保存待复习节点的阶段与可变进度；已掌握目标与掌握边界只存在于结果快照，
    不生成可勾选行。``progress_revision`` 供进度更新的乐观锁使用。
    """

    __tablename__ = "review_plan_node"
    __table_args__ = (
        Index("idx_plan_node_stage", "plan_id", "stage_index"),
        Index("idx_plan_node_concept", "graph_version_id", "concept_id"),
        CheckConstraint("stage_index >= 1", name="ck_plan_node_stage"),
        CheckConstraint(
            "progress_status IN ('NOT_STARTED', 'IN_PROGRESS', 'COMPLETED')",
            name="ck_plan_node_status",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "M05 方案节点：待复习节点的阶段与方案内进度",
        },
    )

    plan_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), primary_key=True, comment="所属方案；逻辑引用 review_plan.id"
    )
    graph_version_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        nullable=False,
        comment="方案固定版本，必须与方案一致（应用层校验）",
    )
    concept_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), primary_key=True, comment="待复习概念"
    )
    stage_index: Mapped[int] = mapped_column(
        mysql.SMALLINT(unsigned=True), nullable=False, comment="复习阶段序号，从 1 开始"
    )
    progress_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'NOT_STARTED'"),
        comment="进度：NOT_STARTED / IN_PROGRESS / COMPLETED",
    )
    progress_revision: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True),
        nullable=False,
        server_default=text("0"),
        comment="进度乐观锁版本号",
    )
    # 与 schema.sql 的 ON UPDATE 保持一致，写法说明见 identity/models.py。
    updated_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"),
        comment="更新时间",
    )
