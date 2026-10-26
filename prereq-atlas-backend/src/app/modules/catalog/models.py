"""M02 知识目录的持久化模型。

对应 ``database/schema.sql`` 中的 ``concept`` 与三个版本内容表。概念稳定身份
与版本内容分开建模：掌握声明和方案引用 ``concept.id``，名称等内容随版本变化。
模型不声明 ``relationship``，跨表引用由服务层在同一事务中校验。
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base


class Concept(Base):
    """概念稳定身份数据对象（DO），对应 ``concept`` 表。

    名称、简介等显示内容随版本放在 ``concept_revision``；本表只保存跨版本
    不变的身份与来源命名空间，避免把不同数据集的同名编号当成同一概念。
    """

    __tablename__ = "concept"
    __table_args__ = (
        UniqueConstraint("code", name="uk_concept_code"),
        UniqueConstraint("source_namespace", "source_key", name="uk_concept_source"),
        CheckConstraint(
            "(source_namespace IS NULL AND source_key IS NULL)"
            " OR (source_namespace IS NOT NULL AND source_key IS NOT NULL)",
            name="ck_concept_source_pair",
        ),
        CheckConstraint("CHAR_LENGTH(TRIM(code)) BETWEEN 1 AND 64", name="ck_concept_code_len"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "M02 概念稳定身份：跨版本不变的知识对象",
        },
    )

    id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=True,
        comment="概念稳定身份主键",
    )
    code: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="系统内概念编码，全局唯一"
    )
    source_namespace: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="来源命名空间，如 AL-CPL/data_mining"
    )
    source_key: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="来源节点编号，如 N001"
    )
    created_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
        comment="创建时间",
    )


class ChapterRevision(Base):
    """章节版本数据对象（DO），对应 ``chapter_revision`` 表。

    章节属于某一图谱版本；``display_order`` 是来源建议的授课顺序，
    不是学生方案中的复习阶段。
    """

    __tablename__ = "chapter_revision"
    __table_args__ = (
        CheckConstraint("CHAR_LENGTH(TRIM(name)) > 0", name="ck_chapter_revision_name"),
        CheckConstraint(
            "CHAR_LENGTH(TRIM(chapter_code)) BETWEEN 1 AND 32", name="ck_chapter_revision_code"
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "M02 章节版本：版本内的课程章节与展示顺序",
        },
    )

    version_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        primary_key=True,
        comment="所属图谱版本；逻辑引用 graph_version.id",
    )
    chapter_code: Mapped[str] = mapped_column(
        String(32), primary_key=True, comment="章节编码，如 M01"
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="章节名称")
    display_order: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True),
        nullable=False,
        server_default=text("0"),
        comment="展示顺序（来源模块顺序）",
    )


class ConceptRevision(Base):
    """概念版本数据对象（DO），对应 ``concept_revision`` 表。

    联合主键 ``(version_id, concept_id)`` 也是关系、资料和方案节点的引用目标，
    服务层据此校验“跨版本混用节点”。
    """

    __tablename__ = "concept_revision"
    __table_args__ = (
        Index("idx_concept_revision_chapter", "version_id", "chapter_code"),
        Index("idx_concept_revision_concept", "concept_id"),
        Index("idx_concept_revision_label", "version_id", "label_zh"),
        CheckConstraint("CHAR_LENGTH(TRIM(label_zh)) > 0", name="ck_concept_revision_label"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "M02 概念版本：版本内的名称、类型、简介与学习重点",
        },
    )

    version_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        primary_key=True,
        comment="所属图谱版本；逻辑引用 graph_version.id",
    )
    concept_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), primary_key=True, comment="概念稳定身份；逻辑引用 concept.id"
    )
    chapter_code: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="所属章节编码，可为空表示未归章"
    )
    label_zh: Mapped[str] = mapped_column(String(200), nullable=False, comment="中文名称")
    label_en: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="英文名称")
    node_type: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment="概念类型：基础概念/方法/算法/模型/领域概念/评价指标/任务",
    )
    introduction: Mapped[str | None] = mapped_column(Text, nullable=True, comment="简介")
    learning_focus: Mapped[str | None] = mapped_column(Text, nullable=True, comment="学习重点")


class ResourceRevision(Base):
    """参考资料数据对象（DO），对应 ``resource_revision`` 表。

    资料只属于某个概念版本，不跨版本共享；方案快照保存标题与链接副本，
    因此不引用 ``resource_id``。
    """

    __tablename__ = "resource_revision"
    __table_args__ = (
        Index("idx_resource_revision_concept", "version_id", "concept_id"),
        CheckConstraint("CHAR_LENGTH(TRIM(title)) > 0", name="ck_resource_revision_title"),
        CheckConstraint(
            "LEFT(url, 7) = 'http://' OR LEFT(url, 8) = 'https://'",
            name="ck_resource_revision_url",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "M02 参考资料：概念版本内的标题、链接与出处",
        },
    )

    resource_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), primary_key=True, autoincrement=True, comment="资料代理主键"
    )
    version_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=False, comment="所属图谱版本"
    )
    concept_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=False, comment="所属概念"
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False, comment="资料标题")
    url: Mapped[str] = mapped_column(
        String(1000), nullable=False, comment="资料链接，仅允许 http/https"
    )
    source: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="出处与许可说明")
