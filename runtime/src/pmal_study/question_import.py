"""Validação e importação rastreável de questões curadas."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pmal_study.db import transaction
from pmal_study.models import Discipline, QuestionStatus

_ORIGINS = {"official", "adapted", "original"}
_RELEVANCE = {"direct", "style_only", "out_of_scope"}


@dataclass(frozen=True, slots=True)
class ValidationResult:
    valid: bool
    status: str
    eligible_for_study: bool
    errors: tuple[str, ...]
    record: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ImportSummary:
    total: int
    imported: int
    study_bank: int
    style_only: int
    out_of_scope: int
    unresolved: int


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _source_page_exists(
    connection: sqlite3.Connection,
    relative_path: str,
    page_number: int,
    *,
    require_usable: bool,
) -> bool:
    if require_usable:
        row = connection.execute(
            """
            SELECT 1
            FROM source_pages AS page
            JOIN source_documents AS document ON document.id = page.document_id
            WHERE document.relative_path = ? AND page.page_number = ?
              AND page.status IN ('usable', 'bundled_reference')
            """,
            (relative_path, page_number),
        ).fetchone()
    else:
        row = connection.execute(
            """
            SELECT 1
            FROM source_documents
            WHERE relative_path = ? AND page_count >= ?
            """,
            (relative_path, page_number),
        ).fetchone()
    return row is not None


def validate_question(
    record: dict[str, Any], connection: sqlite3.Connection
) -> ValidationResult:
    """Valida uma questão e normaliza estados editoriais seguros."""

    normalized = dict(record)
    errors: list[str] = []
    for field in ("id", "statement", "source_document"):
        if not _has_text(normalized.get(field)):
            errors.append(f"{field}: valor textual obrigatório")

    raw_discipline = normalized.get("discipline")
    try:
        discipline = Discipline(raw_discipline)
    except (TypeError, ValueError):
        errors.append(f"discipline: disciplina fora do escopo: {raw_discipline}")
    else:
        normalized["discipline"] = discipline.value

    origin = normalized.get("origin")
    if origin not in _ORIGINS:
        errors.append(f"origin: valor inválido: {origin}")
    relevance = normalized.get("relevance_to_official_2026")
    if relevance not in _RELEVANCE:
        errors.append(f"relevance_to_official_2026: valor inválido: {relevance}")

    answer = normalized.get("answer")
    if answer is not None and answer not in {"C", "E"}:
        errors.append(f"answer: somente C, E ou null: {answer}")
    if answer is None:
        normalized["status"] = QuestionStatus.NEEDS_ANSWER_KEY.value

    status = normalized.get("status")
    if status not in {item.value for item in QuestionStatus}:
        errors.append(f"status: valor inválido: {status}")

    topic_id = normalized.get("topic_id")
    if status == QuestionStatus.VALIDATED.value:
        if not _has_text(topic_id):
            errors.append("topic_id: obrigatório para questão validada")
        elif connection.execute(
            "SELECT 1 FROM syllabus_topics WHERE id = ?", (topic_id,)
        ).fetchone() is None:
            errors.append(f"topic_id: tópico inexistente: {topic_id}")
        if not _has_text(normalized.get("rationale")):
            errors.append("rationale: obrigatório para questão validada")
        if answer is None:
            errors.append("answer: obrigatório para questão validada")
        if origin == "official":
            if not _has_text(normalized.get("answer_key_source")):
                errors.append(
                    "answer_key_source: obrigatório para questão oficial validada"
                )
            if not _has_text(normalized.get("answer_key_checked_at")):
                errors.append(
                    "answer_key_checked_at: obrigatório para questão oficial validada"
                )

    source_document = normalized.get("source_document")
    source_page = normalized.get("source_page")
    if not isinstance(source_page, int) or source_page <= 0:
        errors.append("source_page: inteiro positivo obrigatório")
    elif _has_text(source_document):
        require_usable = status == QuestionStatus.VALIDATED.value
        if not _source_page_exists(
            connection,
            source_document,
            source_page,
            require_usable=require_usable,
        ):
            requirement = "página utilizável" if require_usable else "fonte catalogada"
            errors.append(
                f"source_page: {requirement} não localizada: "
                f"{source_document}#{source_page}"
            )

    rationale_sources = normalized.get("rationale_sources", [])
    if not isinstance(rationale_sources, list):
        errors.append("rationale_sources: deve ser uma lista")
    else:
        for index, rationale_source in enumerate(rationale_sources):
            if not isinstance(rationale_source, dict):
                errors.append(f"rationale_sources[{index}]: deve ser um objeto")
                continue
            rationale_document = rationale_source.get("source_document")
            rationale_page = rationale_source.get("source_page")
            if (
                not _has_text(rationale_document)
                or not isinstance(rationale_page, int)
                or rationale_page <= 0
                or not _source_page_exists(
                    connection,
                    rationale_document,
                    rationale_page,
                    require_usable=True,
                )
            ):
                errors.append(
                    f"rationale_sources[{index}]: página utilizável não localizada"
                )

    eligible = (
        not errors
        and status == QuestionStatus.VALIDATED.value
        and relevance == "direct"
    )
    return ValidationResult(
        valid=not errors,
        status=str(status),
        eligible_for_study=eligible,
        errors=tuple(errors),
        record=normalized,
    )


def _document_id(
    connection: sqlite3.Connection, relative_path: str
) -> str:
    row = connection.execute(
        "SELECT id FROM source_documents WHERE relative_path = ?", (relative_path,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Fonte não catalogada: {relative_path}")
    return str(row[0])


def _ensure_reference_page(
    connection: sqlite3.Connection,
    relative_path: str,
    page_number: int,
    *,
    allow_placeholder: bool,
) -> str:
    document_id = _document_id(connection, relative_path)
    row = connection.execute(
        "SELECT 1 FROM source_pages WHERE document_id = ? AND page_number = ?",
        (document_id, page_number),
    ).fetchone()
    if row is None and allow_placeholder:
        connection.execute(
            """
            INSERT INTO source_pages(document_id, page_number, text, status)
            VALUES (?, ?, '', 'ocr_required')
            """,
            (document_id, page_number),
        )
    return document_id


def import_questions(path: Path, connection: sqlite3.Connection) -> ImportSummary:
    """Importa um JSONL revisado, rejeitando o lote inteiro se houver erro."""

    lines = [
        line
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    validated: list[ValidationResult] = []
    seen_ids: set[str] = set()
    for line_number, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"Linha {line_number}: JSON inválido: {error.msg}") from error
        if not isinstance(record, dict):
            raise ValueError(f"Linha {line_number}: registro deve ser objeto JSON")
        question_id = record.get("id")
        if question_id in seen_ids:
            raise ValueError(f"Linha {line_number}: id duplicado: {question_id}")
        seen_ids.add(question_id)
        result = validate_question(record, connection)
        if not result.valid:
            raise ValueError(f"Linha {line_number}: {'; '.join(result.errors)}")
        validated.append(result)

    with transaction(connection):
        for result in validated:
            record = result.record
            connection.execute(
                """
                INSERT INTO questions(
                    id, origin, year, cargo, number, discipline, topic_id,
                    statement, answer, rationale, status, relevance,
                    adaptation_note, validated_at,
                    answer_key_source, answer_key_checked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    origin = excluded.origin,
                    year = excluded.year,
                    cargo = excluded.cargo,
                    number = excluded.number,
                    discipline = excluded.discipline,
                    topic_id = excluded.topic_id,
                    statement = excluded.statement,
                    answer = excluded.answer,
                    rationale = excluded.rationale,
                    status = excluded.status,
                    relevance = excluded.relevance,
                    adaptation_note = excluded.adaptation_note,
                    validated_at = excluded.validated_at,
                    answer_key_source = excluded.answer_key_source,
                    answer_key_checked_at = excluded.answer_key_checked_at
                """,
                (
                    record["id"],
                    record["origin"],
                    record.get("year"),
                    record.get("cargo"),
                    record.get("number"),
                    record["discipline"],
                    record.get("topic_id"),
                    record["statement"].strip(),
                    record.get("answer"),
                    record.get("rationale"),
                    result.status,
                    record["relevance_to_official_2026"],
                    record.get("adaptation_note"),
                    record.get("answer_key_checked_at")
                    if result.status == QuestionStatus.VALIDATED.value
                    else None,
                    record.get("answer_key_source"),
                    record.get("answer_key_checked_at"),
                ),
            )
            connection.execute(
                "DELETE FROM question_sources WHERE question_id = ?",
                (record["id"],),
            )
            connection.execute(
                """
                INSERT INTO question_sources(
                    question_id, document_id, page_number, source_role
                ) VALUES (?, ?, ?, 'question')
                """,
                (
                    record["id"],
                    _ensure_reference_page(
                        connection,
                        record["source_document"],
                        record["source_page"],
                        allow_placeholder=(
                            result.status != QuestionStatus.VALIDATED.value
                        ),
                    ),
                    record["source_page"],
                ),
            )
            for rationale_source in record.get("rationale_sources", []):
                connection.execute(
                    """
                    INSERT INTO question_sources(
                        question_id, document_id, page_number, source_role
                    ) VALUES (?, ?, ?, 'rationale')
                    """,
                    (
                        record["id"],
                        _document_id(connection, rationale_source["source_document"]),
                        rationale_source["source_page"],
                    ),
                )

    return ImportSummary(
        total=len(validated),
        imported=len(validated),
        study_bank=sum(result.eligible_for_study for result in validated),
        style_only=sum(
            result.record["relevance_to_official_2026"] == "style_only"
            for result in validated
        ),
        out_of_scope=sum(
            result.record["relevance_to_official_2026"] == "out_of_scope"
            for result in validated
        ),
        unresolved=sum(
            result.status != QuestionStatus.VALIDATED.value for result in validated
        ),
    )
