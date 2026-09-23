"""Inicialização de um runtime distribuível sem carregar os PDFs originais."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from pmal_study.db import transaction
from pmal_study.question_import import import_questions
from pmal_study.syllabus import import_syllabus


def _records(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted((root / "corpus" / "questions" / "reviewed").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"Registro inválido no runtime: {path}")
                records.append(value)
    return records


def bootstrap_bundled_runtime(root: Path, connection: sqlite3.Connection) -> bool:
    """Popula uma instalação sem PDFs com referências de fonte, edital e questões."""

    marker = Path(root) / "config" / "bundled_runtime.json"
    if not marker.exists():
        return False
    if connection.execute("SELECT count(*) FROM questions").fetchone()[0] > 0:
        return False
    project_root = Path(root).resolve()
    records = _records(project_root)
    if connection.execute("SELECT count(*) FROM syllabus_topics").fetchone()[0] == 0:
        import_syllabus(project_root / "config" / "syllabus_official.json", connection)

    references: dict[str, set[int]] = {}
    for record in records:
        references.setdefault(str(record["source_document"]), set()).add(
            int(record["source_page"])
        )
        for source in record.get("rationale_sources", []):
            references.setdefault(str(source["source_document"]), set()).add(
                int(source["source_page"])
            )

    with transaction(connection):
        for relative_path, pages in sorted(references.items()):
            document_id = hashlib.sha256(
                relative_path.casefold().encode("utf-8")
            ).hexdigest()
            connection.execute(
                """
                INSERT INTO source_documents(
                    id, relative_path, document_type, sha256, page_count,
                    status, extraction_diagnostics
                ) VALUES (?, ?, 'bundled-reference', ?, ?, 'metadata_only', ?)
                """,
                (
                    document_id,
                    relative_path,
                    hashlib.sha256(f"bundled:{relative_path}".encode()).hexdigest(),
                    max(pages),
                    "PDF original permanece no acervo local de desenvolvimento; esta instalação contém apenas a referência de página.",
                ),
            )
            for page_number in sorted(pages):
                connection.execute(
                    """
                    INSERT INTO source_pages(document_id, page_number, text, status)
                    VALUES (?, ?, '', 'bundled_reference')
                    """,
                    (document_id, page_number),
                )

    for path in sorted((project_root / "corpus" / "questions" / "reviewed").glob("*.jsonl")):
        import_questions(path, connection)
    return True
