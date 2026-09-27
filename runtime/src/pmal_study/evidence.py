"""Recuperação local por escopo, metadados e FTS5."""

from __future__ import annotations

import re
import sqlite3
import unicodedata

from pmal_study.models import Discipline, EvidenceRef


class EvidenceScopeError(ValueError):
    pass


_AUTHORITY_SCORE = {
    "official_legislation": 100,
    "official_court": 95,
    "official_institution": 90,
    "exam_board": 80,
    "local_official_copy": 75,
    "academic": 60,
    "didactic": 40,
}


def _terms(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    normalized = "".join(character for character in normalized if not unicodedata.combining(character))
    return list(dict.fromkeys(re.findall(r"[a-z0-9]{3,}", normalized)))


def search_evidence(
    connection: sqlite3.Connection,
    *,
    discipline: str,
    topic_id: str,
    query: str,
    limit: int = 8,
) -> tuple[EvidenceRef, ...]:
    """Busca evidências atuais sem permitir troca silenciosa de escopo."""

    try:
        selected_discipline = Discipline(discipline).value
    except ValueError as error:
        raise EvidenceScopeError(f"Disciplina fora do escopo: {discipline}") from error
    topic = connection.execute(
        "SELECT discipline, title FROM syllabus_topics WHERE id = ?", (topic_id,)
    ).fetchone()
    if topic is None:
        raise EvidenceScopeError(f"Tópico inexistente: {topic_id}")
    if topic[0] != selected_discipline:
        raise EvidenceScopeError(
            f"O tópico {topic_id} não pertence à disciplina {selected_discipline}."
        )
    query_terms = _terms(f"{query} {topic[1]}")
    if not query_terms:
        raise EvidenceScopeError("A consulta precisa conter termos pesquisáveis.")
    expression = " OR ".join(f'"{term}"' for term in query_terms[:20])
    rows = connection.execute(
        """
        SELECT chunk.id, document.relative_path, chunk.page_start, chunk.locator,
               chunk.text, chunk.sha256, chunk.authority, chunk.topic_id,
               chunk.discipline, bm25(source_chunks_fts)
        FROM source_chunks_fts
        JOIN source_chunks AS chunk ON chunk.rowid = source_chunks_fts.rowid
        JOIN source_documents AS document ON document.id = chunk.document_id
        JOIN source_document_versions AS version ON version.id = chunk.version_id
        WHERE source_chunks_fts MATCH ?
          AND version.is_current = 1
          AND chunk.status IN ('usable', 'ocr')
          AND chunk.discipline = ?
        ORDER BY bm25(source_chunks_fts)
        LIMIT ?
        """,
        (expression, selected_discipline, max(limit * 8, 24)),
    ).fetchall()
    if not rows:
        rows = connection.execute(
            """
            SELECT chunk.id, document.relative_path, chunk.page_start, chunk.locator,
                   chunk.text, chunk.sha256, chunk.authority, chunk.topic_id,
                   chunk.discipline, 0.0
            FROM source_chunks AS chunk
            JOIN source_documents AS document ON document.id = chunk.document_id
            JOIN source_document_versions AS version ON version.id = chunk.version_id
            WHERE version.is_current = 1 AND chunk.status IN ('usable', 'ocr')
              AND chunk.topic_id = ? AND chunk.discipline = ?
            ORDER BY chunk.rowid LIMIT ?
            """,
            (topic_id, selected_discipline, max(limit * 4, 12)),
        ).fetchall()
    desired_terms = set(_terms(query))

    def score(row: tuple[object, ...]) -> float:
        locator_terms = set(_terms(str(row[3] or "")))
        text_terms = set(_terms(str(row[4])))
        overlap = len(desired_terms & (locator_terms | text_terms)) / max(len(desired_terms), 1)
        return (
            _AUTHORITY_SCORE.get(str(row[6]), 20)
            + (60 if row[7] == topic_id else 0)
            + (15 if row[8] == selected_discipline else 0)
            + (25 * overlap)
            + (10 if desired_terms & locator_terms else 0)
            - min(abs(float(row[9] or 0)), 20)
        )

    ranked = sorted(rows, key=score, reverse=True)[: max(1, min(limit, 20))]
    return tuple(
        EvidenceRef(
            id=str(row[0]),
            kind="local",
            source=str(row[1]),
            page=int(row[2]),
            locator=str(row[3] or f"p. {row[2]}"),
            excerpt=str(row[4])[:2400],
            sha256=str(row[5]),
            authority=str(row[6]),
        )
        for row in ranked
    )
