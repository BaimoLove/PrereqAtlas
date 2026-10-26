"""非开发环境拒绝占位 JWT 密钥。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.infra.config import Settings, get_settings
from app.main import create_app


def _settings(**kwargs: object) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[arg-type]


def test_development_allows_placeholder_secret() -> None:
    settings = _settings(app_env="dev", jwt_secret="change-me")
    assert settings.app_env == "dev"


@pytest.mark.parametrize("secret", ["change-me", "", "   ", "changeme", "secret"])
def test_non_development_rejects_placeholder_secret(secret: str) -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        _settings(app_env="prod", jwt_secret=secret)


def test_non_development_rejects_short_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        _settings(app_env="prod", jwt_secret="s" * 31)


def test_non_development_accepts_long_secret() -> None:
    settings = _settings(app_env="prod", jwt_secret="s" * 32)
    assert len(settings.jwt_secret) == 32


def test_create_app_fails_at_startup_with_placeholder_in_non_dev(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("JWT_SECRET", "change-me")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError, match="JWT_SECRET"):
            create_app()
    finally:
        monkeypatch.delenv("APP_ENV", raising=False)
        monkeypatch.delenv("JWT_SECRET", raising=False)
        get_settings.cache_clear()
