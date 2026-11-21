"""测试环境：独立的临时 SQLite，不触碰开发数据库，不启动调度器。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["APP_ENV"] = "test"
os.environ["SCHEDULER_ENABLED"] = "0"
_TMP = Path(tempfile.mkdtemp(prefix="invest_test_"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "0"

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import pytest  # noqa: E402

from app.core.db import SessionLocal, init_db  # noqa: E402
from app.services.seed_service import seed_all  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def prepared_database():
    init_db()
    with SessionLocal() as db:
        seed_all(db)
    yield


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="session")
def client(prepared_database):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
