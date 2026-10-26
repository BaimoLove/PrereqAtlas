"""M08 数据导入的持久化模型。

对应 ``database/schema.sql`` 的批次、原始节点与原始边三张表。原始值原样保存，
解析与映射结果单独成列，清洗不得覆盖来源内容；暂存成功不等于发布。
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Index, Numeric, String, UniqueConstraint, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base


class ImportBatch(Base):
    """导入批次数据对象（DO），对应 ``import_batch`` 表。

    保存两个源文件的 SHA-256、来源说明与解析报告；状态机为
    STAGED -> CONSUMED 或 STAGED -> FAILED。
    """

    __tablename__ = "import_batch"
    __table_args__ = (
        Index("idx_import_batch_operator", "operator_id", "created_at"),
        Index("idx_import_batch_status", "status", "id"),
        CheckConstraint(
            "status IN ('STAGED', 'FAILED', 'CONSUMED')", name="ck_import_batch_status"
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "M08 导入批次：源文件摘要、来源说明与解析报告",
        },
    )

    id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), primary_key=True, autoincrement=True, comment="批次主键"
    )
    operator_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=False, comment="导入操作人；逻辑引用 app_user.id"
    )
    source_note: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="来源、版本与许可说明"
    )
    node_sha256: Mapped[str] = mapped_column(
        mysql.CHAR(64, charset="ascii", collation="ascii_bin"),
        nullable=False,
        comment="节点文件 SHA-256",
    )
    edge_sha256: Mapped[str] = mapped_column(
        mysql.CHAR(64, charset="ascii", collation="ascii_bin"),
        nullable=False,
        comment="边文件 SHA-256",
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'STAGED'"),
        comment="状态：STAGED 已暂存 / FAILED 解析失败 / CONSUMED 已被草稿接收",
    )
    report_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True, comment="解析报告：错误、警告、计数与映射结果"
    )
    created_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
        comment="创建时间",
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=3), nullable=True, comment="解析完成时间"
    )


class ImportRawNode(Base):
    """导入原始节点数据对象（DO），对应 ``import_raw_node`` 表。

    ``raw_json`` 原样保存来源字段；``mapping_status`` 与 ``mapped_concept_id``
    记录映射结果，批次内来源编号唯一。
    """

    __tablename__ = "import_raw_node"
    __table_args__ = (
        UniqueConstraint("batch_id", "logical_record_no", name="uk_import_node_line"),
        Index("idx_import_node_concept", "mapped_concept_id"),
        CheckConstraint(
            "mapping_status IN ('UNMAPPED', 'MAPPED', 'CONFLICT', 'IGNORED')",
            name="ck_import_node_mapping",
        ),
        CheckConstraint(
            "mapping_status <> 'MAPPED' OR mapped_concept_id IS NOT NULL",
            name="ck_import_node_mapped",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "M08 导入原始节点：来源值、映射状态与逻辑记录号",
        },
    )

    batch_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), primary_key=True, comment="所属批次；逻辑引用 import_batch.id"
    )
    source_node_key: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="来源节点编号，如 N001"
    )
    logical_record_no: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True),
        nullable=False,
        comment="原始文件逻辑记录号（含表头，从 1 计）",
    )
    raw_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, comment="原始字段值，未经清洗覆盖"
    )
    mapping_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'UNMAPPED'"),
        comment="映射状态：UNMAPPED/MAPPED/CONFLICT/IGNORED",
    )
    mapped_concept_id: Mapped[int | None] = mapped_column(
        mysql.BIGINT(unsigned=True),
        nullable=True,
        comment="映射到的概念稳定身份；逻辑引用 concept.id",
    )


class ImportRawEdge(Base):
    """导入原始边数据对象（DO），对应 ``import_raw_edge`` 表。

    方向为“先修 -> 后继”，与来源文件列序可能相反，转换时记录；
    ``structural_type`` 与 ``source_strength`` 是来源原值，不作为规划权重。
    """

    __tablename__ = "import_raw_edge"
    __table_args__ = (
        UniqueConstraint("batch_id", "logical_record_no", name="uk_import_edge_line"),
        Index("idx_import_edge_source", "batch_id", "source_node_key"),
        Index("idx_import_edge_target", "batch_id", "target_node_key"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "M08 导入原始边：方向、来源结构分类与强度原值",
        },
    )

    batch_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), primary_key=True, comment="所属批次；逻辑引用 import_batch.id"
    )
    source_edge_key: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="来源边编号，如 E001"
    )
    logical_record_no: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True),
        nullable=False,
        comment="原始文件逻辑记录号（含表头，从 1 计）",
    )
    source_node_key: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="先修端来源节点编号"
    )
    target_node_key: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="后继端来源节点编号"
    )
    structural_type: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="来源结构分类：direct 直接 / transitive 传递推导"
    )
    source_strength: Mapped[Decimal | None] = mapped_column(
        Numeric(3, 2), nullable=True, comment="来源强度原值（1.00 / 0.60），不解释为必要性或权重"
    )
    raw_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, comment="原始字段值，未经清洗覆盖"
    )
    review_note: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="人工核验备注"
    )
