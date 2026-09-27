"""Contrato JSON de linha única para o núcleo local PMAL."""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sqlite3
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pmal_study.corpus import prepare_question_corpus
from pmal_study.bootstrap import bootstrap_bundled_runtime
from pmal_study.db import open_database
from pmal_study.generation import GenerationError, GenerationService
from pmal_study.live_sources import (
    SourcePolicyError,
    register_live_evidence,
    verify_official_source,
)
from pmal_study.markdown_import import import_markdown
from pmal_study.question_import import import_questions
from pmal_study.reports import write_canvas_summary
from pmal_study.sessions import DomainError, StudyService
from pmal_study.sources import catalog_sources, refresh_corpus
from pmal_study.syllabus import import_syllabus


COMMANDS = (
    "start-session",
    "open-study-panel",
    "prefetch-next-item",
    "submit-and-prepare",
    "record-dissection-exchange",
    "request-generation-job",
    "request-dissection-job",
    "get-host-job-status",
    "claim-host-job",
    "complete-generation-job",
    "complete-dissection-job",
    "fail-host-job",
    "end-session",
    "next-question",
    "prepare-next-item",
    "search-evidence",
    "register-live-evidence",
    "commit-generated-question",
    "refresh-corpus",
    "set-exemplar",
    "submit-answer",
    "dashboard",
    "review-queue",
    "export-canvas",
    "catalog-sources",
    "import-syllabus",
    "import-questions",
    "import-markdown",
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
    connection: sqlite3.Connection | None = None,
) -> Any:
    if command == "validate-corpus":
        return prepare_question_corpus(project_root, database_path).to_dict()

    owns_connection = connection is None
    connection = connection or open_database(database_path)
    try:
        if owns_connection:
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
        if command == "open-study-panel":
            return service.open_study_panel(
                mode=str(payload["mode"]) if payload.get("mode") else None,
                duration_minutes=int(payload["duration_minutes"]) if payload.get("duration_minutes") is not None else None,
                disciplines=payload.get("disciplines"),
                operation_id=str(payload["operation_id"]) if payload.get("operation_id") else None,
            )
        if command == "prefetch-next-item":
            return service.public_prepared_item(
                service.prefetch_next_item(str(payload["session_id"]), str(payload["current_question_id"]))
            )
        if command == "submit-and-prepare":
            return service.submit_and_prepare(
                str(payload["session_id"]), str(payload["question_id"]),
                str(payload["answer"]), int(payload["confidence"]), str(payload["attempt_id"]),
                payload.get("error_pattern"),
            )
        if command == "record-dissection-exchange":
            return service.record_dissection_exchange(
                str(payload["attempt_id"]), str(payload["exchange_id"]),
                str(payload["user_message"]), str(payload["assistant_message"]),
                str(payload.get("model", "codex")),
            )
        if command == "request-generation-job":
            return service.request_generation_job(str(payload["job_id"]))
        if command == "request-dissection-job":
            return service.request_dissection_job(
                str(payload["attempt_id"]), str(payload["user_message"]), str(payload["request_id"]),
            )
        if command == "get-host-job-status":
            return service.get_host_job_status(str(payload["request_id"]))
        if command == "claim-host-job":
            return service.claim_host_job(str(payload["request_id"]))
        if command == "complete-generation-job":
            draft = payload.get("draft")
            if not isinstance(draft, dict):
                raise DomainError("invalid_draft", "O campo draft deve ser um objeto.")
            return service.complete_generation_job(
                str(payload["request_id"]), draft, str(payload.get("model", "codex")),
            )
        if command == "complete-dissection-job":
            return service.complete_dissection_job(
                str(payload["request_id"]), str(payload["assistant_message"]),
                str(payload.get("model", "codex")),
            )
        if command == "fail-host-job":
            return service.fail_host_job(
                str(payload["request_id"]), str(payload["code"]), str(payload["message"]),
            )
        if command == "end-session":
            return service.end_session(str(payload["session_id"]), str(payload["operation_id"]))
        if command == "next-question":
            return service.prepare_next_item(str(payload["session_id"]))
        if command == "prepare-next-item":
            return service.prepare_next_item(str(payload["session_id"]))
        if command == "search-evidence":
            return service.search_generation_evidence(
                str(payload["job_id"]), str(payload["query"])
            )
        if command == "register-live-evidence":
            required = {"job_id", "url", "locator", "excerpt"}
            if missing := required - payload.keys():
                raise DomainError(
                    "live_evidence_required",
                    f"Campos obrigatórios ausentes: {', '.join(sorted(missing))}.",
                )
            try:
                result = register_live_evidence(
                    str(payload["url"]), str(payload["locator"]),
                    str(payload["excerpt"]), connection,
                    author=str(payload["author"]) if payload.get("author") else None,
                )
            except SourcePolicyError as error:
                raise DomainError("live_source_policy", str(error)) from error
            GenerationService(connection).attach_live(str(payload["job_id"]), result.id)
            return result
        if command == "commit-generated-question":
            draft = payload.get("draft")
            if not isinstance(draft, dict):
                raise DomainError("invalid_draft", "O campo draft deve ser um objeto.")
            return service.commit_generated_question(str(payload["job_id"]), draft)
        if command == "refresh-corpus":
            source_root = Path(os.environ.get("PMAL_SOURCE_ROOT", project_root)).resolve()
            tessdata = source_root / ".pmal-study" / "ocr" / "tessdata"
            return refresh_corpus(
                source_root, connection,
                tessdata_dir=tessdata if (tessdata / "por.traineddata").is_file() else None,
            )
        if command == "set-exemplar":
            return service.set_exemplar(
                str(payload["question_id"]), str(payload["action"]),
                actor=str(payload.get("actor", "user")),
                reason=str(payload["reason"]) if payload.get("reason") else None,
            )
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
                raise DomainError("path_required", "O caminho Markdown é obrigatório.")
            path = project_root / payload["path"]
            if path.suffix.casefold() != ".md":
                raise DomainError(
                    "markdown_required",
                    "A entrada manual aceita exclusivamente arquivos Markdown (.md).",
                )
            return import_markdown(path, project_root, connection)
        if command == "import-markdown":
            if "path" not in payload:
                raise DomainError("path_required", "O caminho Markdown é obrigatório.")
            return import_markdown(project_root / payload["path"], project_root, connection)
        raise DomainError("unknown_command", "Comando desconhecido.")
    finally:
        if owns_connection:
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
    except GenerationError as error:
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
