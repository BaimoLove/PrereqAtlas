"""应用配置：从环境变量与 .env 读取，缺省值仅用于本地开发。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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


@lru_cache
def get_settings() -> Settings:
    """进程内复用同一份配置，避免每次请求重复解析 .env。"""
    return Settings()
