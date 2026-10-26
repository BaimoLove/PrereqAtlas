"""数据库连接基础层：引擎、会话工厂与事务边界。

MySQL 是持久化来源；会话只在基础设施层创建，业务模块通过 ``session_scope``
获得“一次请求/一次用例一个事务”的语义，不自行装配引擎。
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.infra.config import get_settings


class Base(DeclarativeBase):
    """所有表的声明基类。

    各模块的 ``models.py`` 只从这个基类继承，类与列的元数据汇总在
    ``Base.metadata`` 上供迁移和测试使用；不在此声明任何具体表。
    """


@lru_cache
def get_engine() -> Engine:
    """进程内复用同一引擎；不在此连接数据库，失败留给首次真正使用时暴露。"""
    return create_engine(get_settings().database_url, pool_pre_ping=True, pool_recycle=3600)


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    """会话工厂绑定同一个引擎，供 session_scope 与测试复用。"""
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@contextmanager
def session_scope(
    factory: sessionmaker[Session] | None = None,
) -> Iterator[Session]:
    """一次事务边界：正常退出提交，异常时由关闭会话回滚并继续抛出。

    ``factory`` 供集成测试绑定独立测试库；生产调用不传，使用应用会话工厂。
    """
    session = (factory or get_session_factory())()
    try:
        yield session
        session.commit()
    finally:
        session.close()
