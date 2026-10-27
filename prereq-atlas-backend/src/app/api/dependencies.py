"""请求事务依赖。"""
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.common.contracts import Actor, ErrorCode, Role
from app.infra.db import session_scope
from app.infra.errors import AppError
from app.modules.identity.public import resolve_actor


def get_session() -> Iterator[Session]:
    with session_scope() as session:
        yield session


_bearer = HTTPBearer(auto_error=False)


def get_actor(
    token: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[Session, Depends(get_session, scope="function")],
) -> Actor:
    return resolve_actor(session, token.credentials if token else "")


def require_admin(actor: Annotated[Actor, Depends(get_actor)]) -> Actor:
    if actor.role != Role.ADMIN:
        raise AppError(ErrorCode.FORBIDDEN)
    return actor


def require_student(actor: Annotated[Actor, Depends(get_actor)]) -> Actor:
    if actor.role != Role.STUDENT:
        raise AppError(ErrorCode.FORBIDDEN)
    return actor
