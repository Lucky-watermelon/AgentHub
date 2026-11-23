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
    db_pool_size: int = 20         # 连接池基础大小（压测发现默认 5 在并发 20 时耗尽）
    db_max_overflow: int = 40
    db_pool_timeout: int = 10      # 取连接超时（秒），快速失败优于长时间挂起

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
    llm_max_tokens: int = 512       # 单次生成上限，控制成本
    llm_temperature: float = 0.2
    llm_disable_thinking: bool = True  # DeepSeek 默认开启思考模式，结构化任务关闭更省更快

    # 运行
    run_timeout_s: int = 300
    run_workers: int = 4          # 执行器 worker 数
    run_queue_size: int = 32      # 排队上限，超出返回 AH-RUNTIME-429
    seed_demo: bool = True

    # Langfuse 旁路（可选）：关闭时零开销，不影响主链路
    langfuse_enabled: bool = False
    langfuse_host: str = "http://127.0.0.1:3100"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_sampling: float = 0.2

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
