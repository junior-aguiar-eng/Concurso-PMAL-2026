"""Contrato JSON de linha única para o núcleo local PMAL."""

from __future__ import annotations

import argparse
import dataclasses
import json
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pmal_study.corpus import prepare_question_corpus
from pmal_study.bootstrap import bootstrap_bundled_runtime
from pmal_study.db import open_database
from pmal_study.live_sources import SourcePolicyError, verify_official_source
from pmal_study.question_import import import_questions
from pmal_study.reports import write_canvas_summary
from pmal_study.sessions import DomainError, StudyService
from pmal_study.sources import catalog_sources
from pmal_study.syllabus import import_syllabus


COMMANDS = (
    "start-session",
    "next-question",
    "submit-answer",
    "dashboard",
    "review-queue",
    "export-canvas",
    "catalog-sources",
    "import-syllabus",
    "import-questions",
    "validate-corpus",
    "check-official-source",
)


def _json_value(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {
            field.name: _json_value(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _payload(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise DomainError("invalid_json", "O input-json não contém JSON válido.") from error
    if not isinstance(value, dict):
        raise DomainError("invalid_request", "O input-json deve conter um objeto.")
    return value


def _dispatch(
    command: str,
    payload: dict[str, Any],
    project_root: Path,
    database_path: Path,
) -> Any:
    if command == "validate-corpus":
        return prepare_question_corpus(project_root, database_path).to_dict()

    connection = open_database(database_path)
    try:
        bootstrap_bundled_runtime(project_root, connection)
        service = StudyService(connection)
        if command == "start-session":
            session = service.start_session(
                payload.get("mode", "timed"),
                payload.get("duration_minutes"),
                payload.get("disciplines"),
            )
            data = _json_value(session)
            data["session_id"] = data.pop("id")
            return data
        if command == "next-question":
            return service.next_question(str(payload["session_id"]))
        if command == "submit-answer":
            return service.submit_answer(
                str(payload["session_id"]),
                str(payload["question_id"]),
                str(payload["answer"]),
                int(payload["confidence"]),
                attempt_id=(
                    str(payload["attempt_id"]) if payload.get("attempt_id") else None
                ),
                error_pattern=payload.get("error_pattern"),
            )
        if command == "dashboard":
            return service.get_dashboard()
        if command == "review-queue":
            return service.get_review_queue()
        if command == "export-canvas":
            destination = project_root / ".pmal-study" / "exports" / "painel-pmal-oficial.md"
            return {
                "markdown": write_canvas_summary(
                    connection, datetime.now(UTC), destination
                )
            }
        if command == "catalog-sources":
            return catalog_sources(project_root, connection)
        if command == "check-official-source":
            if "url" not in payload or "citation" not in payload:
                raise DomainError(
                    "source_reference_required", "URL e citação são obrigatórias."
                )
            try:
                return verify_official_source(
                    str(payload["url"]), str(payload["citation"]), connection
                )
            except SourcePolicyError as error:
                raise DomainError("official_source_policy", str(error)) from error
        if command == "import-syllabus":
            path = project_root / payload.get(
                "path", "config/syllabus_official.json"
            )
            return {"imported": import_syllabus(path, connection)}
        if command == "import-questions":
            if "path" not in payload:
                raise DomainError("path_required", "O caminho JSONL é obrigatório.")
            return import_questions(project_root / payload["path"], connection)
        raise DomainError("unknown_command", "Comando desconhecido.")
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--database", type=Path)
    parser.add_argument("--input-json", default="{}")
    arguments = parser.parse_args(argv)
    root = arguments.project_root.resolve()
    database = arguments.database or root / ".pmal-study" / "pmal-study.db"
    try:
        data = _dispatch(
            arguments.command,
            _payload(arguments.input_json),
            root,
            database,
        )
    except DomainError as error:
        _emit(
            {
                "ok": False,
                "error": {"code": error.code, "message": str(error)},
            }
        )
        return 2
    except (KeyError, TypeError, ValueError) as error:
        _emit(
            {
                "ok": False,
                "error": {"code": "invalid_request", "message": str(error)},
            }
        )
        return 2
    _emit({"ok": True, "data": _json_value(data)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
