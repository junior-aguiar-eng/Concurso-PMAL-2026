"""Preparação e auditoria do corpus curado de questões."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pmal_study.db import open_database
from pmal_study.question_import import ImportSummary, import_questions
from pmal_study.sources import (
    CatalogResult,
    catalog_sources,
    extract_document_pages,
)
from pmal_study.syllabus import import_syllabus


@dataclass(frozen=True, slots=True)
class CorpusAudit:
    total: int
    study_bank: int
    style_only: int
    out_of_scope: int
    unresolved: int
    violations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CorpusBuildReport:
    catalog: CatalogResult
    imported: int
    audit: CorpusAudit
    extracted_pages: int
    failed_extractions: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "catalog": asdict(self.catalog),
            "imported": self.imported,
            "audit": asdict(self.audit),
            "extracted_pages": self.extracted_pages,
            "failed_extractions": self.failed_extractions,
        }


def audit_question_corpus(
    connection: sqlite3.Connection, expected_ids: set[str]
) -> CorpusAudit:
    violations: list[str] = []
    columns = (
        "id",
        "status",
        "relevance",
        "answer",
        "rationale",
        "topic_id",
        "answer_key_source",
        "answer_key_checked_at",
    )
    rows = {
        row[0]: dict(zip(columns, row, strict=True))
        for row in connection.execute(
            "SELECT {} FROM questions WHERE id IN ({})".format(
                ", ".join(columns),
                ",".join("?" for _ in expected_ids) or "NULL"
            ),
            tuple(sorted(expected_ids)),
        ).fetchall()
    }
    for missing in sorted(expected_ids - set(rows)):
        violations.append(f"{missing}: não importada")

    for question_id, row in sorted(rows.items()):
        if row["status"] != "validated":
            continue
        required = (
            "answer",
            "rationale",
            "topic_id",
            "answer_key_source",
            "answer_key_checked_at",
        )
        for field in required:
            if row[field] is None or (isinstance(row[field], str) and not row[field].strip()):
                violations.append(f"{question_id}: campo obrigatório ausente: {field}")
        roles = {
            source[0]
            for source in connection.execute(
                """
                SELECT link.source_role
                FROM question_sources AS link
                JOIN source_pages AS page
                  ON page.document_id = link.document_id
                 AND page.page_number = link.page_number
                WHERE link.question_id = ? AND page.status = 'usable'
                """,
                (question_id,),
            ).fetchall()
        }
        if "question" not in roles:
            violations.append(f"{question_id}: sem fonte utilizável da questão")
        if "rationale" not in roles:
            violations.append(f"{question_id}: sem fonte de fundamentação")

    values = list(rows.values())
    return CorpusAudit(
        total=len(values),
        study_bank=sum(
            row["status"] == "validated" and row["relevance"] == "direct"
            for row in values
        ),
        style_only=sum(row["relevance"] == "style_only" for row in values),
        out_of_scope=sum(row["relevance"] == "out_of_scope" for row in values),
        unresolved=sum(row["status"] != "validated" for row in values),
        violations=tuple(violations),
    )


def _load_records(paths: list[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError(f"{path}:{line_number}: registro não é objeto")
            records.append(payload)
    identifiers = [record.get("id") for record in records]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("O corpus contém identificadores duplicados.")
    return records


def _references(records: list[dict[str, Any]]) -> dict[str, set[int]]:
    references: dict[str, set[int]] = {}
    for record in records:
        references.setdefault(record["source_document"], set()).add(
            record["source_page"]
        )
        for source in record.get("rationale_sources", []):
            references.setdefault(source["source_document"], set()).add(
                source["source_page"]
            )
    return references


def prepare_question_corpus(
    project_root: Path,
    database_path: Path | None = None,
) -> CorpusBuildReport:
    root = Path(project_root).resolve()
    db_path = database_path or root / ".pmal-study" / "pmal-study.db"
    connection = open_database(db_path)
    try:
        catalog = catalog_sources(root, connection)
        if connection.execute("SELECT count(*) FROM syllabus_topics").fetchone()[0] == 0:
            import_syllabus(root / "config" / "syllabus_official.json", connection)
        paths = sorted((root / "corpus" / "questions" / "reviewed").glob("*.jsonl"))
        records = _load_records(paths)

        extracted_pages = failed_extractions = 0
        for relative_path, pages in sorted(_references(records).items()):
            row = connection.execute(
                "SELECT id FROM source_documents WHERE relative_path = ?",
                (relative_path,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Fonte do corpus não catalogada: {relative_path}")
            result = extract_document_pages(row[0], pages, connection, root)
            extracted_pages += result.usable_pages
            failed_extractions += result.failed_pages

        summaries: list[ImportSummary] = [
            import_questions(path, connection) for path in paths
        ]
        audit = audit_question_corpus(
            connection,
            {str(record["id"]) for record in records},
        )
        return CorpusBuildReport(
            catalog=catalog,
            imported=sum(summary.imported for summary in summaries),
            audit=audit,
            extracted_pages=extracted_pages,
            failed_extractions=failed_extractions,
        )
    finally:
        connection.close()
