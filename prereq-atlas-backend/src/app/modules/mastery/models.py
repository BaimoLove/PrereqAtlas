"""M03 掌握声明的持久化模型。

对应 ``database/schema.sql`` 的 ``user_mastery`` 表。声明属于“学生-稳定概念”，
不随方案变化；是否存在记录即“是否已声明掌握”，不表达测评结论。
"""

from datetime import datetime

from sqlalchemy import Index, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base


class UserMastery(Base):
    """掌握声明数据对象（DO），对应 ``user_mastery`` 表。

    联合主键 ``(user_id, concept_id)`` 保证同一学生对同一概念最多一条声明；
    ``declared_version_id`` 记录声明时采用的图谱版本，供方案快照追溯。
    """

    __tablename__ = "user_mastery"
    __table_args__ = (
        Index("idx_user_mastery_concept", "concept_id"),
        Index("idx_user_mastery_version", "declared_version_id"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "M03 掌握声明：学生对稳定概念的自评掌握",
        },
    )

    user_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), primary_key=True, comment="学生用户；逻辑引用 app_user.id"
    )
    concept_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), primary_key=True, comment="概念稳定身份；逻辑引用 concept.id"
    )
    declared_version_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        nullable=False,
        comment="声明时使用的图谱版本；逻辑引用 graph_version.id",
    )
    declared_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
        comment="声明时间",
    )
