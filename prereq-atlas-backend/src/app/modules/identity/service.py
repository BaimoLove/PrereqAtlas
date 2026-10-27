"""身份用例；调用方提供事务，账号数据仅由 identity 访问。"""
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.common.contracts import Actor, ErrorCode, Role, validate_id_string
from app.common.http_contracts import RegisterRequest, TokenForm, TokenResponse, UserSummary
from app.common.time import now_beijing
from app.infra.config import get_settings
from app.infra.errors import AppError
from app.modules.identity.models import AppUser
from app.modules.identity.security import hash_password, verify_password


def _create_account(
    session: Session, credentials: RegisterRequest, role: Role,
) -> UserSummary:
    user = AppUser(username=credentials.username,
                   password_hash=hash_password(credentials.password), role=role)
    session.add(user)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        if getattr(exc.orig, "args", (None,))[0] == 1062:
            raise AppError(ErrorCode.CONFLICT, "用户名已存在") from exc
        raise
    return UserSummary(id=str(user.id), username=user.username, role=Role(user.role))


def register_student(session: Session, credentials: RegisterRequest) -> UserSummary:
    return _create_account(session, credentials, Role.STUDENT)


def create_admin(session: Session, credentials: RegisterRequest) -> UserSummary:
    return _create_account(session, credentials, Role.ADMIN)


def authenticate(session: Session, credentials: TokenForm) -> TokenResponse:
    user = session.scalar(select(AppUser).where(
        AppUser.username_normalized == credentials.username.strip().lower(),
    ))
    if (
        user is None
        or not verify_password(credentials.password, user.password_hash)
        or not user.is_active
    ):
        raise AppError(ErrorCode.AUTHENTICATION_REQUIRED, "用户名或密码错误")
    settings = get_settings()
    now = datetime.now(UTC)
    expires_in = settings.access_token_expire_minutes * 60
    token = jwt.encode(
        {"sub": str(user.id), "iat": now, "exp": now + timedelta(seconds=expires_in),
         "token_version": user.token_version},
        settings.jwt_secret, algorithm=settings.jwt_algorithm,
    )
    user.last_login_at = now_beijing().replace(tzinfo=None)
    return TokenResponse(access_token=token, expires_in=expires_in)


def resolve_actor(session: Session, token: str) -> Actor:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "exp", "iat", "token_version"]},
        )
        user_id = int(validate_id_string(payload["sub"]))
        if type(payload["token_version"]) is not int or payload["token_version"] < 0:
            raise ValueError("Invalid token version")
    except (jwt.InvalidTokenError, ValueError, TypeError, KeyError) as exc:
        raise AppError(ErrorCode.AUTHENTICATION_REQUIRED) from exc
    user = session.get(AppUser, user_id)
    if user is None or not user.is_active or payload.get("token_version") != user.token_version:
        raise AppError(ErrorCode.AUTHENTICATION_REQUIRED)
    return Actor(user_id=user.id, role=Role(user.role), token_version=user.token_version)


def get_active_user(session: Session, user_id: int) -> UserSummary:
    user = session.get(AppUser, user_id)
    if user is None or not user.is_active:
        raise AppError(ErrorCode.AUTHENTICATION_REQUIRED)
    return UserSummary(id=str(user.id), username=user.username, role=Role(user.role))
