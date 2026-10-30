from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLATFORM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PLATFORM))

os.environ["SECRET_KEY"] = "test-secret-key-please-use-env-32chars"
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///" + str(ROOT / "platform" / "tests" / "_test.db").replace("\\", "/")
os.environ["ADMIN_EMAIL"] = "admin@example.com"
os.environ["ADMIN_PASSWORD"] = "adminpass1"
os.environ["FILE_ROOT"] = str(ROOT / "platform" / "tests" / "_files")
os.environ["FENGSUN_WORKER"] = "0"
os.environ["FENGSUN_LLM_STUB"] = "1"
os.environ["FENGSUN_SKIP_HEAVY"] = "1"
os.environ["FENGSUN_SKIP_MAIL"] = "1"

from fengsuan.app.core.config import get_settings

get_settings.cache_clear()

from fastapi.testclient import TestClient
from fengsuan.app.db.session import reset_engine, init_db, SessionLocal
from fengsuan.app.main import create_app
from fengsuan.app.services.seed import ensure_seed

import pytest


@pytest.fixture()
def client(tmp_path):
    db_path = tmp_path / "t.db"
    os.environ["DATABASE_URL"] = "sqlite+pysqlite:///" + str(db_path).replace("\\", "/")
    os.environ["FILE_ROOT"] = str(tmp_path / "files")
    get_settings.cache_clear()
    reset_engine()
    init_db()
    db = SessionLocal()
    try:
        ensure_seed(db)
    finally:
        db.close()
    app = create_app()
    with TestClient(app) as c:
        yield c
    reset_engine()
    get_settings.cache_clear()


def admin_token(client: TestClient) -> str:
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]
