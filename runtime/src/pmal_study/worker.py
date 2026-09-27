"""Worker JSONL persistente para o bridge MCP local."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from pmal_study.bootstrap import bootstrap_bundled_runtime
from pmal_study.cli import _dispatch, _json_value
from pmal_study.db import open_database
from pmal_study.generation import GenerationError
from pmal_study.migrations import SCHEMA_VERSION
from pmal_study.sessions import DomainError


def _write(payload: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--database", type=Path)
    arguments = parser.parse_args(argv)
    root = arguments.project_root.resolve()
    database = arguments.database or root / ".pmal-study" / "pmal-study.db"
    connection = open_database(database)
    try:
        bootstrap_bundled_runtime(root, connection)
        _write({"type": "ready", "protocol": 1, "pid": os.getpid(), "schema": SCHEMA_VERSION})
        for raw in sys.stdin:
            request_id: object = None
            try:
                request = json.loads(raw)
                request_id = request.get("id")
                command = str(request["command"])
                payload = request.get("payload", {})
                if not isinstance(payload, dict):
                    raise DomainError("invalid_request", "O payload deve ser um objeto.")
                if command == "worker-info":
                    data = {"pid": os.getpid(), "connection_id": id(connection), "schema": SCHEMA_VERSION}
                else:
                    data = _dispatch(command, payload, root, database, connection=connection)
                _write({"id": request_id, "ok": True, "data": _json_value(data)})
            except (DomainError, GenerationError) as error:
                _write({"id": request_id, "ok": False, "error": {"code": error.code, "message": str(error)}})
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                _write({"id": request_id, "ok": False, "error": {"code": "invalid_request", "message": str(error)}})
            except Exception as error:
                _write({"id": request_id, "ok": False, "error": {"code": "worker_failure", "message": str(error)}})
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
