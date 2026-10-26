"""M01 账号与权限的持久化模型。

对应 ``database/schema.sql`` 的 ``app_user`` 表；列顺序、约束与索引与建表脚本
一一对应。模型不声明 ``relationship``：本项目的表间引用只在服务层校验，
跨模块关联由 ``public.py`` 的用例完成，模型之间不互相依赖。
"""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, Computed, Index, String, UniqueConstraint, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base


class AppUser(Base):
    """账号数据对象（DO），对应 ``app_user`` 表。

    管理员与学生共用一张表，以 ``role`` 区分；注册只创建学生账号。
    不保存明文口令，只有 Argon2 散列；``token_version`` 递增可作废旧令牌。
    """

    __tablename__ = "app_user"
    __table_args__ = (
        UniqueConstraint("username_normalized", name="uk_user_username"),
        Index("idx_user_role_active", "role", "is_active"),
        CheckConstraint("role IN ('STUDENT', 'ADMIN')", name="ck_user_role"),
        CheckConstraint("is_active IN (0, 1)", name="ck_user_is_active"),
        CheckConstraint(
            "CHAR_LENGTH(TRIM(username)) BETWEEN 1 AND 64", name="ck_user_username_len"
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "M01 账号：学生与管理员账号、角色、启用状态与令牌版本",
        },
    )

    id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), primary_key=True, autoincrement=True, comment="用户主键"
    )
    username: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="登录名，保留用户输入的原始大小写"
    )
    # 生成列：只读，由数据库按 username 计算，应用层不写入。
    username_normalized: Mapped[str | None] = mapped_column(
        String(64),
        Computed("lower(trim(username))", persisted=True),
        nullable=True,
        comment="规范化登录名：去首尾空格并转小写，由数据库生成",
    )
    password_hash: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="口令散列（Argon2），禁止存明文"
    )
    role: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'STUDENT'"),
        comment="角色：STUDENT 学生 / ADMIN 管理员",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("1"), comment="账号是否启用：1 启用 / 0 停用"
    )
    token_version: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True),
        nullable=False,
        server_default=text("0"),
        comment="令牌版本，递增后旧令牌失效",
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=3), nullable=True, comment="最近一次登录成功时间"
    )
    created_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
        comment="创建时间",
    )
    # SQLAlchemy 没有能落成 DDL 的 onupdate 参数，把 ON UPDATE 一并写进默认值，
    # 使建模生成的列定义与 database/schema.sql 完全一致。
    updated_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"),
        comment="更新时间",
    )
