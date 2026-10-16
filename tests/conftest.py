from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from db import connect
from loader.load_garmin import load
from loader.make_fixture import build, write

AS_OF = date(2026, 9, 13)


@pytest.fixture(scope="session")
def export_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("export")
    write(root, build(AS_OF))
    return root


@pytest.fixture(scope="session")
def db_file(tmp_path_factory: pytest.TempPathFactory, export_root: Path) -> Path:
    path = tmp_path_factory.mktemp("db") / "test.duckdb"
    with connect(path) as conn:
        load(export_root, conn)
    return path


@pytest.fixture()
def conn(db_file: Path):
    connection = connect(db_file, read_only=True)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture(autouse=True)
def journal_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test gets its own journal; nothing ever writes into journals/."""
    path = tmp_path / "test.journal.duckdb"
    monkeypatch.setenv("JOURNAL_DB", str(path))
    return path


@pytest.fixture()
def client(db_file: Path, monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("WEARABLE_DB", str(db_file))
    from tools.server import app

    with TestClient(app) as test_client:
        yield test_client
