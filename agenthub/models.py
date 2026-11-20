"""ORM 模型：智能体、技能、工具、运行、知识库、模型接入、凭证。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def now() -> datetime:
    return datetime.now()


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(400), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft / published / canary
    dag_json: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class AgentVersion(Base):
    __tablename__ = "agent_versions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    trigger: Mapped[str] = mapped_column(String(80), default="")
    gate: Mapped[str] = mapped_column(String(20), default="PENDING")  # PASS / BLOCK / PENDING
    note: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(20), default="业务")
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    prompt: Mapped[str] = mapped_column(String(200), default="")
    model: Mapped[str] = mapped_column(String(60), default="default-chat")
    tools_json: Mapped[str] = mapped_column(Text, default="[]")
    timeout_s: Mapped[int] = mapped_column(Integer, default=30)
    retry: Mapped[int] = mapped_column(Integer, default=1)
    refs: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class SkillVersion(Base):
    __tablename__ = "skill_versions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    trigger: Mapped[str] = mapped_column(String(80), default="")
    gate: Mapped[str] = mapped_column(String(20), default="PENDING")
    changelog: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Tool(Base):
    __tablename__ = "tools"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    source: Mapped[str] = mapped_column(String(20), default="内置")  # 内置 / MCP / 自定义
    impl: Mapped[str] = mapped_column(String(20), default="Python")  # Python / HTTP / Jinja2
    permission: Mapped[str] = mapped_column(String(30), default="read")
    timeout_s: Mapped[int] = mapped_column(Integer, default=10)
    idempotency: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="启用")
    schema_json: Mapped[str] = mapped_column(Text, default="{}")
    refs: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tool_name: Mapped[str] = mapped_column(String(120), index=True)
    trace_id: Mapped[str] = mapped_column(String(40), index=True, default="")
    run_id: Mapped[str] = mapped_column(String(40), index=True, default="")
    params: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(20), default="成功")
    dur_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(40), index=True)
    agent_name: Mapped[str] = mapped_column(String(120), default="")
    agent_version: Mapped[int] = mapped_column(Integer, default=1)
    customer_no: Mapped[str] = mapped_column(String(60), default="")
    status: Mapped[str] = mapped_column(String(30), default="CREATED")
    trace_id: Mapped[str] = mapped_column(String(40), index=True, default="")
    thread_id: Mapped[str] = mapped_column(String(40), default="")
    idempotency_key: Mapped[str] = mapped_column(String(120), default="", index=True)
    state_json: Mapped[str] = mapped_column(Text, default="")        # HITL 中断时的运行状态快照
    node_progress: Mapped[str] = mapped_column(Text, default="")     # {"completed": [...], "interrupted_at": "n_x"}
    input_json: Mapped[str] = mapped_column(Text, default="{}")
    output_json: Mapped[str] = mapped_column(Text, default="{}")
    features_json: Mapped[str] = mapped_column(Text, default="{}")
    report_path: Mapped[str] = mapped_column(String(300), default="")
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    dur_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(String(400), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RunEvent(Base):
    __tablename__ = "run_events"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    event: Mapped[str] = mapped_column(String(40))
    data: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Checkpoint(Base):
    __tablename__ = "checkpoints"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    step: Mapped[int] = mapped_column(Integer, default=0)
    node: Mapped[str] = mapped_column(String(120), default="")
    state_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class KbDoc(Base):
    __tablename__ = "kb_docs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), default="口径")
    status: Mapped[str] = mapped_column(String(20), default="已索引")
    chunks: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(String(300), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class KbChunk(Base):
    __tablename__ = "kb_chunks"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    doc_id: Mapped[str] = mapped_column(ForeignKey("kb_docs.id"), index=True)
    idx: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    vector_json: Mapped[str] = mapped_column(Text, default="[]")


class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    kind: Mapped[str] = mapped_column(String(20), default="本地")  # 本地 / 云端 / 规则
    base_url: Mapped[str] = mapped_column(String(300), default="")
    models: Mapped[str] = mapped_column(String(300), default="")
    caps: Mapped[str] = mapped_column(String(200), default="聊天 / 流式")
    status: Mapped[str] = mapped_column(String(20), default="healthy")
    api_key: Mapped[str] = mapped_column(String(200), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Alias(Base):
    __tablename__ = "aliases"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    alias: Mapped[str] = mapped_column(String(60), unique=True)
    provider_name: Mapped[str] = mapped_column(String(80), default="rule")
    model: Mapped[str] = mapped_column(String(120), default="")
    caps: Mapped[str] = mapped_column(String(200), default="聊天 / 流式")
    status: Mapped[str] = mapped_column(String(20), default="启用")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class AppKey(Base):
    __tablename__ = "app_keys"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    app: Mapped[str] = mapped_column(String(80), default="")
    type: Mapped[str] = mapped_column(String(20), default="AppKey")
    prefix: Mapped[str] = mapped_column(String(40), default="")
    digest: Mapped[str] = mapped_column(String(80), default="")
    status: Mapped[str] = mapped_column(String(20), default="启用")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    expires_at: Mapped[str] = mapped_column(String(30), default="")


class QuotaPolicy(Base):
    """按调用凭证（AppKey / PAT）维度的限额策略。"""

    __tablename__ = "quota_policies"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    app_key_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    rps: Mapped[int] = mapped_column(Integer, default=10)          # 每秒请求数上限，0 表示不限
    concurrency: Mapped[int] = mapped_column(Integer, default=4)   # 并发上限，0 表示不限
    daily_calls: Mapped[int] = mapped_column(Integer, default=1000)  # 日调用次数上限，0 表示不限
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class QuotaUsage(Base):
    """按天累计的调用量，用于日配额判定与用量展示。"""

    __tablename__ = "quota_usage"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    app_key_id: Mapped[str] = mapped_column(String(40), index=True)
    day: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD
    calls: Mapped[int] = mapped_column(Integer, default=0)
    blocks: Mapped[int] = mapped_column(Integer, default=0)   # 被限流拦截次数
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)
