"""应用配置：从环境变量与 .env 读取，缺省值仅用于本地开发。"""

from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PLACEHOLDER_SECRETS = frozenset({"", "change-me", "changeme", "secret", "test", "password"})
_MIN_NON_DEV_SECRET_LENGTH = 32


class Settings(BaseSettings):
    """运行时配置项。缺省值可让应用在无 .env 时启动，不代表可部署配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "PrereqAtlas · 先修图谱与复习规划系统 API"
    app_env: str = "dev"
    api_v1_prefix: str = "/api/v1"

    # 以下三项在持久化与认证模块落地后才会真正使用。
    database_url: str = "mysql+pymysql://root:change-me@127.0.0.1:3306/prereq_atlas?charset=utf8mb4"
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    @model_validator(mode="after")
    def _validate_non_dev_secret(self) -> Settings:
        """非开发环境拒绝占位或过短的 JWT 密钥，启动即失败而非带病运行。"""
        if self.app_env.strip().lower() != "dev":
            secret = self.jwt_secret.strip()
            if secret.lower() in _PLACEHOLDER_SECRETS or len(secret) < _MIN_NON_DEV_SECRET_LENGTH:
                raise ValueError(
                    "非开发环境必须配置长度至少 32 且非占位的 JWT_SECRET"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    """进程内复用同一份配置，避免每次请求重复解析 .env。"""
    return Settings()
