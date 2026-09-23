"""Fixtures comuns para a suíte de testes do motor PMAL."""

import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
import pytest

from pmal_study.db import open_database
from pmal_study.bootstrap import bootstrap_bundled_runtime


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


@pytest.fixture
def db_connection(project_root: Path) -> sqlite3.Connection:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_file = Path(temp_dir) / "test-pmal.db"
        conn = open_database(db_file)
        bootstrap_bundled_runtime(project_root, conn)
        try:
            yield conn
        finally:
            conn.close()


@pytest.fixture
def frozen_now() -> datetime:
    return datetime(2026, 9, 22, 12, 0, 0, tzinfo=UTC)
