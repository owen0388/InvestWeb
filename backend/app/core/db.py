"""数据库连接与会话管理。开发默认 SQLite，切换 PostgreSQL 只需改 DATABASE_URL。"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import DATA_DIR, settings

DATA_DIR.mkdir(parents=True, exist_ok=True)

connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
    future=True,
)

if settings.DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragma(dbapi_connection, _record):  # pragma: no cover - 由驱动决定
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    """FastAPI 依赖注入。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """后台任务使用：成功提交、异常回滚。"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# 新增列清单：(表名, 列名, SQL 类型)。
# SQLite 的 create_all 只建表不加列，老库升级需要显式补齐；正式方案是 Alembic（P1）。
# 只允许"加列"，不允许在这里改类型或删列——那属于迁移，必须走脚本。
COLUMN_PATCHES: list[tuple[str, str, str]] = [
    ("etf_universe", "quality_confidence", "FLOAT"),
    ("etf_universe", "quality_breakdown", "JSON"),
    ("etf_universe", "premium_basis", "VARCHAR(16)"),
    # Phase 1：行情来源与复权口径。旧数据无法自证口径，标记为 legacy_em
    ("market_observations", "source_provider", "VARCHAR(32)"),
    ("market_observations", "adjustment_type", "VARCHAR(8)"),
]


def _ensure_columns() -> None:
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, column, ddl_type in COLUMN_PATCHES:
            if not inspector.has_table(table):
                continue
            existing = {col["name"] for col in inspector.get_columns(table)}
            if column in existing:
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))


def init_db() -> None:
    # 先导入模型以完成注册
    from app.models import entities  # noqa: F401
    # Phase 1：全市场标的表（新增，不触碰已有 28 张表）
    from app.models import market_models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_columns()
