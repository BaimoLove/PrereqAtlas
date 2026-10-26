"""M06 图谱管理的持久化模型。

对应 ``database/schema.sql`` 中的版本、关系、生效指针与审计四张表。方向固定为
``source_concept_id``（先修）-> ``target_concept_id``（后继）；关系两端与所属
版本一致由发布校验保证，模型不再声明外键或 ``relationship``。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Index, Numeric, String, UniqueConstraint, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base


class GraphVersion(Base):
    """图谱版本数据对象（DO），对应 ``graph_version`` 表。

    草稿可编辑、发布后冻结；``draft_revision`` 是内容写入的乐观锁版本号，
    发布后不原地修改，后续编辑复制成新草稿并用 ``base_version_id`` 记录来源。
    """

    __tablename__ = "graph_version"
    __table_args__ = (
        UniqueConstraint("version_label", name="uk_graph_version_label"),
        Index("idx_graph_version_state", "state", "id"),
        Index("idx_graph_version_base", "base_version_id"),
        Index("idx_graph_version_created_by", "created_by"),
        Index("idx_graph_version_published_by", "published_by"),
        CheckConstraint("state IN ('DRAFT', 'PUBLISHED')", name="ck_graph_version_state"),
        CheckConstraint(
            "(state = 'DRAFT' AND published_at IS NULL AND published_by IS NULL"
            " AND content_hash IS NULL)"
            " OR (state = 'PUBLISHED' AND published_at IS NOT NULL"
            " AND published_by IS NOT NULL AND content_hash IS NOT NULL)",
            name="ck_graph_version_published",
        ),
        CheckConstraint(
            "validated_revision IS NULL OR validated_revision <= draft_revision",
            name="ck_graph_version_validated",
        ),
        CheckConstraint(
            "CHAR_LENGTH(TRIM(version_label)) BETWEEN 1 AND 64",
            name="ck_graph_version_label_len",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "M06 图谱版本：草稿编辑、发布冻结与内容摘要",
        },
    )

    id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), primary_key=True, autoincrement=True, comment="版本主键"
    )
    version_label: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="版本标签，如 v1.0"
    )
    state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'DRAFT'"),
        comment="状态：DRAFT 草稿 / PUBLISHED 已发布",
    )
    base_version_id: Mapped[int | None] = mapped_column(
        mysql.BIGINT(unsigned=True),
        nullable=True,
        comment="草稿的复制来源版本；空表示全新草稿；逻辑引用 graph_version.id",
    )
    draft_revision: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        nullable=False,
        server_default=text("0"),
        comment="草稿乐观锁版本号，每次内容写入递增",
    )
    content_hash: Mapped[str | None] = mapped_column(
        mysql.CHAR(64, charset="ascii", collation="ascii_bin"),
        nullable=True,
        comment="发布时图谱内容 SHA-256（十六进制）",
    )
    validated_revision: Mapped[int | None] = mapped_column(
        mysql.BIGINT(unsigned=True),
        nullable=True,
        comment="最近一次校验通过时的 draft_revision",
    )
    note: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="版本说明")
    created_by: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=False, comment="创建人；逻辑引用 app_user.id"
    )
    created_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
        comment="创建时间",
    )
    published_by: Mapped[int | None] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=True, comment="发布人；逻辑引用 app_user.id"
    )
    published_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=3), nullable=True, comment="发布时间"
    )


class GraphRelation(Base):
    """先修关系数据对象（DO），对应 ``graph_relation`` 表。

    ``necessity`` 是系统采用的依赖语义，``structural_type`` 只是来源结构分类，
    两者不合并；``source_strength`` 保留来源原值，不作为规划权重。
    """

    __tablename__ = "graph_relation"
    __table_args__ = (
        UniqueConstraint(
            "version_id",
            "source_concept_id",
            "target_concept_id",
            "necessity",
            name="uk_graph_relation_edge",
        ),
        Index("idx_graph_relation_source", "version_id", "source_concept_id"),
        Index("idx_graph_relation_target", "version_id", "target_concept_id"),
        Index("idx_graph_relation_review", "version_id", "review_status", "necessity"),
        Index("idx_graph_relation_batch", "source_batch_id"),
        Index("idx_graph_relation_reviewer", "reviewed_by"),
        CheckConstraint("source_concept_id <> target_concept_id", name="ck_graph_relation_no_self"),
        CheckConstraint(
            "necessity IN ('REQUIRED', 'RECOMMENDED', 'OPTIONAL')",
            name="ck_graph_relation_necessity",
        ),
        CheckConstraint(
            "structural_type IS NULL OR structural_type IN ('direct', 'transitive')",
            name="ck_graph_relation_structural",
        ),
        CheckConstraint(
            "source_strength IS NULL OR (source_strength >= 0 AND source_strength <= 1)",
            name="ck_graph_relation_strength",
        ),
        CheckConstraint(
            "review_status IN ('PENDING', 'APPROVED', 'REJECTED')",
            name="ck_graph_relation_review",
        ),
        CheckConstraint(
            "review_status = 'PENDING' OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="ck_graph_relation_reviewed",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "M06 先修关系：版本内先修到后继的依赖、必要性与审核记录",
        },
    )

    id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), primary_key=True, autoincrement=True, comment="关系主键"
    )
    version_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        nullable=False,
        comment="所属图谱版本；逻辑引用 graph_version.id",
    )
    source_concept_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        nullable=False,
        comment="先修概念；与 version_id 一起逻辑引用 concept_revision",
    )
    target_concept_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        nullable=False,
        comment="后继概念；与 version_id 一起逻辑引用 concept_revision",
    )
    necessity: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'REQUIRED'"),
        comment="必要性：REQUIRED 强制 / RECOMMENDED 推荐 / OPTIONAL 可选关联",
    )
    structural_type: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="来源结构分类：direct / transitive，仅说明来源"
    )
    source_strength: Mapped[Decimal | None] = mapped_column(
        Numeric(3, 2), nullable=True, comment="来源强度原值，不作为规划权重"
    )
    review_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'PENDING'"),
        comment="审核状态：PENDING / APPROVED / REJECTED",
    )
    rationale: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="纳入或审核理由"
    )
    source_batch_id: Mapped[int | None] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=True, comment="来源导入批次；逻辑引用 import_batch.id"
    )
    source_edge_key: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="来源边编号，如 E001"
    )
    reviewed_by: Mapped[int | None] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=True, comment="审核人；逻辑引用 app_user.id"
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=3), nullable=True, comment="审核时间"
    )
    created_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
        comment="创建时间",
    )
    # 与 schema.sql 的 ON UPDATE 保持一致，写法说明见 identity/models.py。
    updated_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"),
        comment="更新时间",
    )


class ActiveGraph(Base):
    """生效指针数据对象（DO），对应 ``active_graph`` 表。

    固定单例行，指向当前对学生规划生效的已发布版本；只在发布事务中更新，
    目标版本必须是 PUBLISHED。
    """

    __tablename__ = "active_graph"
    __table_args__ = (
        Index("idx_active_graph_version", "version_id"),
        Index("idx_active_graph_user", "updated_by"),
        CheckConstraint("singleton_id = 1", name="ck_active_graph_singleton"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "M06 生效指针：单例记录当前发布版本，未发布时为空",
        },
    )

    singleton_id: Mapped[int] = mapped_column(
        mysql.TINYINT(unsigned=True),
        primary_key=True,
        autoincrement=False,
        server_default=text("1"),
        comment="单例主键，恒为 1",
    )
    version_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        nullable=False,
        comment="当前生效版本；逻辑引用 graph_version.id",
    )
    revision: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        nullable=False,
        server_default=text("0"),
        comment="指针乐观锁版本号",
    )
    updated_by: Mapped[int | None] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=True, comment="最近更新人；逻辑引用 app_user.id"
    )
    # 与 schema.sql 的 ON UPDATE 保持一致，写法说明见 identity/models.py。
    updated_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"),
        comment="更新时间",
    )


class GraphAudit(Base):
    """图谱操作审计数据对象（DO），对应 ``graph_audit`` 表。

    每次草稿创建、内容与关系修改、校验、发布都追加一条；不修改历史行。
    """

    __tablename__ = "graph_audit"
    __table_args__ = (
        Index("idx_graph_audit_version", "version_id", "created_at", "id"),
        Index("idx_graph_audit_operator", "operator_id", "created_at"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "M06 图谱操作审计：草稿与发布全过程记录",
        },
    )

    id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), primary_key=True, autoincrement=True, comment="审计主键"
    )
    version_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        nullable=False,
        comment="操作的图谱版本；逻辑引用 graph_version.id",
    )
    operator_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=False, comment="操作人；逻辑引用 app_user.id"
    )
    action: Mapped[str] = mapped_column(
        String(48),
        nullable=False,
        comment="动作：CREATE_DRAFT/EDIT_CONTENT/EDIT_RELATION/IMPORT/VALIDATE/PUBLISH/DELETE_DRAFT",
    )
    revision_before: Mapped[int | None] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=True, comment="操作前 draft_revision"
    )
    revision_after: Mapped[int | None] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=True, comment="操作后 draft_revision"
    )
    summary: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="操作摘要，不写入口令或令牌"
    )
    created_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
        comment="操作时间",
    )
