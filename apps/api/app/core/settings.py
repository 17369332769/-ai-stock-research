"""应用配置。密钥只从环境变量或本地 secret 文件注入，不写入数据库或日志（spec §14.3）。"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

SHANGHAI_TZ = "Asia/Shanghai"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "local"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://app:app@localhost:5432/app"

    # 业务代码只允许经此访问外部数据（spec §4.2）
    openbb_base_url: str = "http://127.0.0.1:6900"
    openbb_timeout_seconds: float = 30.0

    # 行情超过该秒数即为 stale；禁止把旧行情标记为实时（spec §3.2）
    quote_stale_seconds: int = 180

    model_artifact_root: str = "/models"

    # Agent 为可选组件；未配置时降级为模板摘要，不阻断其余功能（spec §11.3）
    agent_base_url: str = ""
    agent_api_key: str = Field(default="", repr=False)
    agent_model: str = ""

    git_sha: str = ""

    @property
    def agent_enabled(self) -> bool:
        return bool(self.agent_base_url and self.agent_model)

    def __repr__(self) -> str:  # 防止密钥进入日志
        return f"Settings(app_env={self.app_env!r}, agent_enabled={self.agent_enabled})"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
