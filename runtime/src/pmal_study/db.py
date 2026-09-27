"""Abertura e controle transacional do banco local."""

from __future__ import annotations

import sqlite3
from hashlib import sha256
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from itertools import count
from pathlib import Path

_SAVEPOINT_SEQUENCE = count(1)


def _is_authoritative_database(path: Path) -> bool:
    return path.name == "pmal-study.db" and path.parent.name == ".pmal-study"


def _backup_before_migration(path: Path, target_version: int) -> Path | None:
    """Cria cópia SQLite consistente antes de alterar um banco persistente."""

    if not path.is_file() or path.stat().st_size == 0:
        return None
    probe = sqlite3.connect(path)
    try:
        current_version = int(probe.execute("PRAGMA user_version").fetchone()[0])
    finally:
        probe.close()
    if current_version <= 0 or current_version >= target_version:
        return None

    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backup_dir / f"{path.stem}.v{current_version}.{timestamp}.db"
    source = sqlite3.connect(path)
    destination = sqlite3.connect(backup_path)
    try:
        source.backup(destination)
        if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError(f"Backup SQLite inválido: {backup_path}")
    except BaseException:
        destination.close()
        source.close()
        backup_path.unlink(missing_ok=True)
        raise
    destination.close()
    source.close()
    return backup_path


def open_database(path: Path) -> sqlite3.Connection:
    """Abre, configura e migra o banco, deixando seu fechamento ao chamador."""

    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    from pmal_study.migrations import SCHEMA_VERSION

    _backup_before_migration(database_path, SCHEMA_VERSION)
    connection = sqlite3.connect(database_path, isolation_level=None)
    try:
        connection.create_function(
            "pmal_sha256",
            1,
            lambda value: sha256(str(value).encode("utf-8")).hexdigest(),
            deterministic=True,
        )
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
