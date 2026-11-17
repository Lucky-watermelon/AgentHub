"""运行配置：全部通过环境变量或 .env 覆盖，默认值面向本地单机。"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AH_", env_file=".env", extra="ignore")

    app_name: str = "AgentHub"
    profile: str = "lite"  # lite / full

    # 数据落盘位置（相对项目根目录，可用绝对路径覆盖）
    data_dir: str = "data"
    reports_dir: str = "reports"
    web_dir: str = "web"
    database_url: str = ""

    # HTTP
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: str = "*"

    # 鉴权：本地开发默认关闭，接入业务系统时置为 true
    auth_required: bool = False

    # 模型调用
    default_model: str = "default-chat"
    llm_timeout_s: int = 60
    llm_mock: bool = True  # true 时不真正出网，用规则化文本兜底

    # 运行
    run_timeout_s: int = 300
    seed_demo: bool = True

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        return p if p.is_absolute() else BASE_DIR / p

    @property
    def reports_path(self) -> Path:
        p = Path(self.reports_dir)
        return p if p.is_absolute() else BASE_DIR / p

    @property
    def web_path(self) -> Path:
        p = Path(self.web_dir)
        return p if p.is_absolute() else BASE_DIR / p

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_path / 'agenthub.db').as_posix()}"

    def ensure_dirs(self) -> None:
        self.data_path.mkdir(parents=True, exist_ok=True)
        self.reports_path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s


settings = get_settings()
