"""数据库连接、会话与初始化（SQLite 默认，可切 PostgreSQL）。"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


IS_SQLITE = settings.sqlalchemy_url.startswith("sqlite")
connect_args = {"check_same_thread": False, "timeout": 15} if IS_SQLITE else {}
engine = create_engine(settings.sqlalchemy_url, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


if IS_SQLITE:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - 连接级配置
        """WAL + busy_timeout：多线程运行与事件写入不再互相阻塞太久。"""
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> list[int]:
    """建表 + 应用迁移，返回本次新应用的迁移版本号。"""
    from . import models  # noqa: F401  确保模型注册
    from .migrations import run_migrations

    Base.metadata.create_all(bind=engine)
    return run_migrations(engine)
