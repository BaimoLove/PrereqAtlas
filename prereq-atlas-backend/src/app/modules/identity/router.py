"""注册 HTTP 入口。"""
from typing import Annotated

from fastapi import APIRouter, Depends, Form
from sqlalchemy.orm import Session

from app.api.dependencies import get_actor, get_session
from app.common.contracts import Actor
from app.common.http_contracts import RegisterRequest, TokenForm, TokenResponse, UserSummary
from app.modules.identity.service import authenticate, get_active_user, register_student

router = APIRouter(tags=["identity"])


@router.post("/auth/register", status_code=201)
def register(
    body: RegisterRequest, session: Annotated[Session, Depends(get_session, scope="function")],
) -> UserSummary:
    return register_student(session, body)


@router.post("/auth/token")
def login(
    body: Annotated[TokenForm, Form()],
    session: Annotated[Session, Depends(get_session, scope="function")],
) -> TokenResponse:
    return authenticate(session, body)


@router.get("/users/me")
def me(
    actor: Annotated[Actor, Depends(get_actor)],
    session: Annotated[Session, Depends(get_session, scope="function")],
) -> UserSummary:
    return get_active_user(session, actor.user_id)
