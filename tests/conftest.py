import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
import app as sitadmin  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Base SQLite nueva y vacía para cada test."""
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()
    conn = database.get_connection()
    yield conn
    conn.close()


@pytest.fixture
def client(db):
    sitadmin.app.config["TESTING"] = True
    with sitadmin.app.test_client() as c:
        yield c
