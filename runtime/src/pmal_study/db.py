"""Abertura e controle transacional do banco local."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from itertools import count
from pathlib import Path

_SAVEPOINT_SEQUENCE = count(1)


def _is_authoritative_database(path: Path) -> bool:
    return path.name == "pmal-study.db" and path.parent.name == ".pmal-study"


def open_database(path: Path) -> sqlite3.Connection:
    """Abre, configura e migra o banco, deixando seu fechamento ao chamador."""

    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        journal_mode = "WAL" if _is_authoritative_database(database_path) else "DELETE"
        connection.execute(f"PRAGMA journal_mode = {journal_mode}")

        from pmal_study.migrations import run_migrations

        run_migrations(connection)
        return connection
    except BaseException:
        connection.close()
        raise


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[None]:
    """Executa uma unidade atômica, usando savepoint quando já há transação."""

    if connection.in_transaction:
        savepoint = f"pmal_sp_{next(_SAVEPOINT_SEQUENCE)}"
        connection.execute(f"SAVEPOINT {savepoint}")
        try:
            yield
        except BaseException:
            connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        else:
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        return

    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")
