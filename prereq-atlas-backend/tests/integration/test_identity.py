"""T05/T06：通过 HTTP、管理员 CLI 和真实 MySQL 验证身份行为。"""
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from app.infra import db
from app.infra.config import get_settings
from app.main import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
def identity_client(migrated_database, monkeypatch) -> Iterator[tuple[TestClient, Engine]]:
    _, engine = migrated_database
    monkeypatch.setenv("JWT_SECRET", "identity-tests-only-signing-key-32-bytes-long")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60")
    get_settings.cache_clear()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(db, "get_session_factory", lambda: factory)
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        yield client, engine
    get_settings.cache_clear()


def test_student_can_register_with_string_id_and_fixed_role(identity_client):
    client, _ = identity_client
    response = client.post("/api/v1/auth/register", json={
        "username": "Alice_01", "password": "Register-pass-123",
    })
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "username", "role"}
    assert isinstance(body["id"], str) and int(body["id"]) > 0
    assert body["username"] == "Alice_01"
    assert body["role"] == "STUDENT"


def test_concurrent_normalized_registration_creates_one_account(identity_client):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy import text

    client, engine = identity_client
    barrier = Barrier(2)

    def register(username):
        barrier.wait(timeout=10)
        return client.post("/api/v1/auth/register", json={
            "username": username, "password": "Concurrent-pass-123",
        })

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(register, ["Same_User", "same_user"]))
    assert sorted(response.status_code for response in responses) == [201, 409]
    rejected = next(response for response in responses if response.status_code == 409)
    assert rejected.json()["code"] == "CONFLICT"
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM app_user")).scalar_one() == 1


@pytest.mark.parametrize(("field", "length", "status"), [
    ("username", 2, 422), ("username", 3, 201), ("username", 4, 201),
    ("username", 31, 201), ("username", 32, 201), ("username", 33, 422),
    ("password", 5, 422), ("password", 6, 201), ("password", 7, 201),
    ("password", 127, 201), ("password", 128, 201), ("password", 129, 422),
])
def test_registration_length_boundaries(identity_client, field, length, status):
    from sqlalchemy import text

    client, engine = identity_client
    payload = {"username": "Boundary_User", "password": "Boundary-pass-123"}
    payload[field] = "a" * length
    response = client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == status
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM app_user")).scalar_one() == (
            1 if status == 201 else 0
        )


@pytest.mark.parametrize("extra", [
    {"role": "ADMIN"}, {"owner": "1"}, {"owner_id": "1"}, {"is_active": True},
    {"token_version": 0},
])
def test_registration_rejects_injected_fields_without_writes(identity_client, extra):
    from sqlalchemy import text

    client, engine = identity_client
    response = client.post("/api/v1/auth/register", json={
        "username": "Forbidden_User", "password": "Forbidden-pass-123", **extra,
    })
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM app_user")).scalar_one() == 0


@pytest.mark.parametrize("username", [" abcd", "abcd ", "a-b", "中文学生", "ab\n", "ＡＢＣ"])
def test_registration_rejects_non_contract_usernames(identity_client, username):
    client, _ = identity_client
    response = client.post("/api/v1/auth/register", json={
        "username": username, "password": "Allowed-pass-123",
    })
    assert response.status_code == 422


def test_registration_stores_verifiable_argon2_without_plaintext_leaks(identity_client, caplog):
    import logging

    from pwdlib import PasswordHash
    from sqlalchemy import text

    client, engine = identity_client
    password = "  Exact-Password-123  "
    caplog.set_level(logging.INFO)
    response = client.post("/api/v1/auth/register", json={
        "username": "Hash_User", "password": password,
    })
    assert response.status_code == 201
    with engine.connect() as connection:
        stored = connection.execute(text("SELECT password_hash FROM app_user")).scalar_one()
    assert stored.startswith("$argon2id$")
    assert PasswordHash.recommended().verify(password, stored)
    assert not PasswordHash.recommended().verify(password.strip(), stored)
    for secret in [password, stored]:
        assert secret not in response.text + caplog.text


def run_create_admin(engine, argv, *, input_text=None):
    """真实 CLI 调用；input_text=None 表示不提供标准输入，模拟非交互终端。"""
    import os
    import subprocess
    import sys

    from support.db import BACKEND_ROOT

    environment = dict(os.environ)
    environment["DATABASE_URL"] = engine.url.render_as_string(hide_password=False)
    # 保留外层 PYTHONPATH：解释器不带 venv 时，子进程仍需导入第三方依赖。
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(BACKEND_ROOT / "src"), os.environ.get("PYTHONPATH")) if part
    )
    environment["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(BACKEND_ROOT / "scripts" / "create_admin.py"), *argv],
        input=input_text,
        stdin=subprocess.DEVNULL if input_text is None else None,
        text=True, encoding="utf-8", errors="replace", capture_output=True,
        env=environment, cwd=BACKEND_ROOT, timeout=30,
    )


def run_admin(engine, username, password):
    return run_create_admin(
        engine, ["--username", username, "--password-stdin"], input_text=password + "\n",
    )


def test_admin_cli_creates_admin_and_duplicate_preserves_password(identity_client):
    from pwdlib import PasswordHash
    from sqlalchemy import text

    _, engine = identity_client
    original = "Admin-Original-123"
    created = run_admin(engine, "Admin_One", original)
    assert created.returncode == 0
    duplicate = run_admin(engine, "admin_one", "Other-Password-123")
    assert duplicate.returncode == 2
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT role,password_hash FROM app_user")).all()
    assert len(rows) == 1
    assert rows[0].role == "ADMIN"
    assert PasswordHash.recommended().verify(original, rows[0].password_hash)
    assert original not in created.stdout + created.stderr + duplicate.stdout + duplicate.stderr


def test_admin_cli_names_the_invalid_field_without_echoing_input(identity_client):
    from sqlalchemy import text

    _, engine = identity_client
    short_password = "short"
    short = run_create_admin(
        engine, ["--username", "Bad_Admin", "--password-stdin"], input_text=short_password + "\n",
    )
    assert short.returncode == 2
    assert "密码需为 6～128 个字符" in short.stderr
    assert short_password not in short.stdout + short.stderr
    bad_username = run_create_admin(
        engine, ["--username", "bad-admin", "--password-stdin"], input_text="Admin-pass-123\n",
    )
    assert bad_username.returncode == 2
    assert "用户名需为 3～32 个 ASCII 字母、数字或下划线" in bad_username.stderr
    assert "Admin-pass-123" not in bad_username.stdout + bad_username.stderr
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM app_user")).scalar_one() == 0


def test_admin_cli_rejects_non_interactive_password_prompt(identity_client):
    from sqlalchemy import text

    _, engine = identity_client
    refused = run_create_admin(engine, ["--username", "Pipe_Admin"])
    assert refused.returncode == 2
    assert "不是交互控制台" in refused.stderr
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM app_user")).scalar_one() == 0


def test_admin_cli_rejects_empty_piped_password(identity_client):
    from sqlalchemy import text

    _, engine = identity_client
    empty = run_create_admin(
        engine, ["--username", "Empty_Admin", "--password-stdin"], input_text="\n",
    )
    assert empty.returncode == 2
    assert "Password is empty" in empty.stderr
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM app_user")).scalar_one() == 0


def test_registered_student_can_login_with_normalized_username_and_read_me(identity_client):
    client, _ = identity_client
    registered = client.post("/api/v1/auth/register", json={
        "username": "Login_Student", "password": "Login-pass-123",
    })
    assert registered.status_code == 201
    login = client.post("/api/v1/auth/token", data={
        "username": "login_student", "password": "Login-pass-123",
    })
    assert login.status_code == 200
    token = login.json()
    assert set(token) == {"access_token", "token_type", "expires_in"}
    assert token["token_type"] == "bearer"
    assert token["expires_in"] == 3600
    me = client.get("/api/v1/users/me", headers={
        "Authorization": "Bearer " + token["access_token"],
    })
    assert me.status_code == 200
    assert me.json() == registered.json()


def test_invalid_credentials_are_indistinguishable(identity_client):
    client, _ = identity_client
    client.post("/api/v1/auth/register", json={
        "username": "Real_User", "password": "Correct-pass-123",
    })
    responses = [client.post("/api/v1/auth/token", data={
        "username": username, "password": "Wrong-pass-123",
    }) for username in ["Real_User", "Missing_User"]]
    assert [response.status_code for response in responses] == [401, 401]
    bodies = [{key: value for key, value in response.json().items() if key != "request_id"}
              for response in responses]
    assert bodies[0] == bodies[1] == {
        "code": "AUTHENTICATION_REQUIRED", "message": "用户名或密码错误", "details": None,
    }


def login_student(client):
    registered = client.post("/api/v1/auth/register", json={
        "username": "Token_User", "password": "Token-pass-123",
    })
    assert registered.status_code == 201
    login = client.post("/api/v1/auth/token", data={
        "username": "Token_User", "password": "Token-pass-123",
    })
    assert login.status_code == 200
    return registered.json(), login.json()["access_token"]


@pytest.mark.parametrize("kind", ["missing", "expired", "forged", "malformed", "basic"])
def test_invalid_bearer_is_unauthorized(identity_client, kind):
    import jwt

    client, _ = identity_client
    _, token = login_student(client)
    headers = {"Authorization": "Bearer " + token}
    if kind == "missing":
        headers = {}
    elif kind == "expired":
        claims = jwt.decode(token, options={"verify_signature": False})
        claims.update(iat=1, exp=2)
        token = jwt.encode(claims, get_settings().jwt_secret, algorithm="HS256")
        headers = {"Authorization": "Bearer " + token}
    elif kind == "forged":
        claims = jwt.decode(token, options={"verify_signature": False})
        token = jwt.encode(claims, "different-test-signing-key-32-bytes-long", algorithm="HS256")
        headers = {"Authorization": "Bearer " + token}
    elif kind == "malformed":
        headers = {"Authorization": "Bearer not-a-jwt"}
    elif kind == "basic":
        headers = {"Authorization": "Basic irrelevant"}
    response = client.get("/api/v1/users/me", headers=headers)
    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_REQUIRED"
    assert response.json()["message"] == "需要有效登录"


@pytest.mark.parametrize("change", ["disabled", "version"])
def test_account_state_invalidates_previously_issued_token(identity_client, change):
    from sqlalchemy import text

    client, engine = identity_client
    user, token = login_student(client)
    assert client.get("/api/v1/users/me", headers={
        "Authorization": "Bearer " + token,
    }).status_code == 200
    statement = ("UPDATE app_user SET is_active=0 WHERE id=:id" if change == "disabled"
                 else "UPDATE app_user SET token_version=token_version+1 WHERE id=:id")
    with engine.begin() as connection:
        connection.execute(text(statement), {"id": int(user["id"])})
    response = client.get("/api/v1/users/me", headers={"Authorization": "Bearer " + token})
    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_REQUIRED"
    relogin = client.post("/api/v1/auth/token", data={
        "username": "Token_User", "password": "Token-pass-123",
    })
    assert relogin.status_code == (401 if change == "disabled" else 200)
    if change == "disabled":
        assert relogin.json()["message"] == "用户名或密码错误"
    if change == "version":
        assert client.get("/api/v1/users/me", headers={
            "Authorization": "Bearer " + relogin.json()["access_token"],
        }).status_code == 200


@pytest.mark.parametrize("role", ["STUDENT", "ADMIN"])
def test_role_dependencies_allow_only_the_exact_role(identity_client, role):
    from typing import Annotated

    from fastapi import Depends

    from app.api import dependencies
    from app.common.contracts import Actor

    client, engine = identity_client
    if role == "ADMIN":
        assert run_admin(engine, "Role_User", "Role-pass-123").returncode == 0
    else:
        assert client.post("/api/v1/auth/register", json={
            "username": "Role_User", "password": "Role-pass-123",
        }).status_code == 201

    @client.app.get("/_test/admin")
    def admin(actor: Annotated[Actor, Depends(dependencies.require_admin)]):
        return {"user_id": str(actor.user_id), "role": actor.role.value}

    @client.app.get("/_test/student")
    def student(actor: Annotated[Actor, Depends(dependencies.require_student)]):
        return {"user_id": str(actor.user_id), "role": actor.role.value}

    login = client.post("/api/v1/auth/token", data={
        "username": "role_user", "password": "Role-pass-123",
    })
    assert login.status_code == 200
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    me = client.get("/api/v1/users/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["role"] == role
    allowed = "/_test/admin" if role == "ADMIN" else "/_test/student"
    forbidden = "/_test/student" if role == "ADMIN" else "/_test/admin"
    assert client.get(allowed, headers=headers).status_code == 200
    denied = client.get(forbidden, headers=headers)
    assert denied.status_code == 403
    assert denied.json()["code"] == "FORBIDDEN"
    assert client.get(allowed).status_code == 401


def test_token_and_responses_expose_only_public_identity_data(identity_client, caplog):
    import logging

    import jwt
    from sqlalchemy import text

    client, engine = identity_client
    caplog.set_level(logging.INFO)
    user, token = login_student(client)
    claims = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    assert set(claims) == {"sub", "iat", "exp", "token_version"}
    assert claims["sub"] == user["id"]
    assert claims["exp"] - claims["iat"] == 3600
    assert claims["token_version"] == 0
    with engine.connect() as connection:
        stored = connection.execute(text("SELECT password_hash FROM app_user")).scalar_one()
        last_login = connection.execute(text("SELECT last_login_at FROM app_user")).scalar_one()
        assert last_login is not None
    response = client.get("/api/v1/users/me", headers={"Authorization": "Bearer " + token})
    assert response.json() == user
    combined = str(claims) + response.text + caplog.text
    for secret in ["Token-pass-123", stored, get_settings().jwt_secret,
                   engine.url.render_as_string(hide_password=False)]:
        assert secret not in combined
    assert token not in caplog.text + response.text


@pytest.mark.parametrize("change", [
    "missing_exp", "missing_iat", "missing_version", "boolean_version", "string_version",
    "invalid_subject", "oversized_subject", "future_iat", "none_algorithm",
])
def test_incomplete_or_invalid_token_claims_are_unauthorized(identity_client, change):
    import jwt

    client, _ = identity_client
    _, token = login_student(client)
    claims = jwt.decode(token, options={"verify_signature": False})
    if change.startswith("missing_"):
        claims.pop({"missing_exp": "exp", "missing_iat": "iat",
                    "missing_version": "token_version"}[change])
    elif change == "boolean_version":
        claims["token_version"] = False
    elif change == "string_version":
        claims["token_version"] = "0"
    elif change == "invalid_subject":
        claims["sub"] = "-1"
    elif change == "oversized_subject":
        claims["sub"] = "18446744073709551616"
    elif change == "future_iat":
        claims["iat"] += 7200
    forged = jwt.encode(claims, None if change == "none_algorithm" else get_settings().jwt_secret,
                        algorithm="none" if change == "none_algorithm" else "HS256")
    response = client.get("/api/v1/users/me", headers={"Authorization": "Bearer " + forged})
    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_REQUIRED"


def test_existing_token_uses_current_database_role(identity_client):
    from typing import Annotated

    from fastapi import Depends
    from sqlalchemy import text

    from app.api.dependencies import require_admin, require_student
    from app.common.contracts import Actor

    client, engine = identity_client
    user, token = login_student(client)

    @client.app.get("/_test/current-admin")
    def admin(actor: Annotated[Actor, Depends(require_admin)]):
        return {"role": actor.role.value}

    @client.app.get("/_test/current-student")
    def student(actor: Annotated[Actor, Depends(require_student)]):
        return {"role": actor.role.value}

    headers = {"Authorization": "Bearer " + token}
    assert client.get("/_test/current-admin", headers=headers).status_code == 403
    with engine.begin() as connection:
        connection.execute(text("UPDATE app_user SET role='ADMIN' WHERE id=:id"),
                           {"id": int(user["id"])})
    assert client.get("/api/v1/users/me", headers=headers).json()["role"] == "ADMIN"
    assert client.get("/_test/current-admin", headers=headers).status_code == 200
    assert client.get("/_test/current-student", headers=headers).status_code == 403


@pytest.mark.parametrize("kind", ["extra_field", "json_instead_of_form"])
def test_token_endpoint_enforces_form_contract(identity_client, kind):
    client, _ = identity_client
    login_student(client)
    data = {"username": "Token_User", "password": "Token-pass-123"}
    if kind == "extra_field":
        response = client.post("/api/v1/auth/token", data={**data, "role": "ADMIN"})
    else:
        response = client.post("/api/v1/auth/token", json=data)
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert data["password"] not in response.text


def test_identity_writes_leave_database_integrity_intact(identity_client):
    from support.db import run_integrity_checks

    client, engine = identity_client
    login_student(client)
    assert run_admin(engine, "Integrity_Admin", "Integrity-pass-123").returncode == 0
    counts = run_integrity_checks(engine)
    assert len(counts) == 31
    assert {name: count for name, count in counts.items() if count} == {}


def test_registration_reports_commit_failure_without_success_or_partial_account(identity_client):
    from sqlalchemy import event, text
    from sqlalchemy.exc import OperationalError

    client, engine = identity_client

    def fail_commit(connection):
        raise OperationalError("COMMIT", {}, RuntimeError("database commit unavailable"))

    event.listen(engine, "commit", fail_commit)
    try:
        response = client.post("/api/v1/auth/register", json={
            "username": "Commit_User", "password": "Commit-pass-123",
        })
    finally:
        event.remove(engine, "commit", fail_commit)
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert "Commit-pass-123" not in response.text
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM app_user")).scalar_one() == 0
    retried = client.post("/api/v1/auth/register", json={
        "username": "Commit_User", "password": "Commit-pass-123",
    })
    assert retried.status_code == 201


def test_successful_login_persists_last_login_at_before_response(identity_client):
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import text

    client, engine = identity_client
    credentials = {"username": "Login_Time_User", "password": "Login-time-pass-123"}
    registered = client.post("/api/v1/auth/register", json=credentials)
    assert registered.status_code == 201
    user_id = int(registered.json()["id"])
    query = text("SELECT last_login_at FROM app_user WHERE id=:id")
    with engine.connect() as connection:
        assert connection.execute(query, {"id": user_id}).scalar_one() is None

    beijing = timezone(timedelta(hours=8))
    started = datetime.now(beijing).replace(tzinfo=None)
    login = client.post("/api/v1/auth/token", data=credentials)
    finished = datetime.now(beijing).replace(tzinfo=None)
    assert login.status_code == 200
    with engine.connect() as connection:
        stored = connection.execute(query, {"id": user_id}).scalar_one()
    assert stored is not None
    assert started - timedelta(milliseconds=1) <= stored <= finished + timedelta(milliseconds=1)

    # 固定旧时间，不依赖 sleep，验证再次登录真的更新数据库中的值。
    previous = datetime(2000, 1, 1)
    with engine.begin() as connection:
        connection.execute(text("UPDATE app_user SET last_login_at=:previous WHERE id=:id"),
                           {"id": user_id, "previous": previous})
    again = client.post("/api/v1/auth/token", data=credentials)
    assert again.status_code == 200
    with engine.connect() as connection:
        updated = connection.execute(query, {"id": user_id}).scalar_one()
    assert updated is not None and updated > previous


@pytest.mark.parametrize("has_previous_login", [False, True])
def test_failed_login_does_not_change_last_login_at(identity_client, has_previous_login):
    from datetime import datetime

    from sqlalchemy import text

    client, engine = identity_client
    credentials = {"username": "Failed_Login_Time", "password": "Correct-time-pass-123"}
    registered = client.post("/api/v1/auth/register", json=credentials)
    assert registered.status_code == 201
    user_id = int(registered.json()["id"])
    previous = datetime(2000, 1, 1) if has_previous_login else None
    if has_previous_login:
        with engine.begin() as connection:
            connection.execute(text("UPDATE app_user SET last_login_at=:previous WHERE id=:id"),
                               {"id": user_id, "previous": previous})
    rejected = client.post("/api/v1/auth/token", data={
        **credentials, "password": "Wrong-time-pass-123",
    })
    assert rejected.status_code == 401
    with engine.connect() as connection:
        stored = connection.execute(
            text("SELECT last_login_at FROM app_user WHERE id=:id"), {"id": user_id},
        ).scalar_one()
    assert stored == previous
