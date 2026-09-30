"""Entrada manual única em Markdown para questões e material doutrinário."""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from pmal_study.db import transaction
from pmal_study.sources import _infer_discipline, enrich_chunk_metadata


@dataclass(frozen=True, slots=True)
class MarkdownImportSummary:
    total_questions: int = 0
    applied_questions: int = 0
    needs_review: int = 0
    style_only: int = 0
    doctrine_documents: int = 0
    doctrine_chunks: int = 0
    documents_unchanged: int = 0


_QUESTION_HEADING = re.compile(r"(?im)^##\s+quest[aã]o(?:\s+([^\n]+))?\s*$")
_SECTION_HEADING = re.compile(r"(?m)^###\s+([^\n]+)\s*$")
_ANY_HEADING = re.compile(r"(?m)^(#{1,6})\s+([^\n]+)\s*$")


def _key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return "".join(character for character in normalized if not unicodedata.combining(character)).strip()


def _sections(body: str) -> dict[str, str]:
    matches = list(_SECTION_HEADING.finditer(body))
    result: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        result[_key(match.group(1))] = body[match.end():end].strip()
    return result


def _value(sections: dict[str, str], *names: str) -> str | None:
    for name in names:
        value = sections.get(_key(name))
        if value and value.strip():
            return value.strip()
    return None


def _discipline(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = _key(value)
    aliases = {
        "direito penal militar": "direito_penal_militar",
        "dpm": "direito_penal_militar",
        "direito processual penal militar": "direito_processual_penal_militar",
        "processo penal militar": "direito_processual_penal_militar",
        "dppm": "direito_processual_penal_militar",
        "cppm": "direito_processual_penal_militar",
        "legislacao pmal": "legislacao_pmal",
        "legislacao da pmal": "legislacao_pmal",
        "conhecimentos de alagoas": "conhecimentos_alagoas",
        "conhecimentos alagoas": "conhecimentos_alagoas",
    }
    return aliases.get(normalized)


def _integer(value: str | None) -> int | None:
    if value is None:
        return None
    match = re.search(r"\d+", value)
    return int(match.group()) if match else None


def _answer(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = _key(value)
    if normalized in {"c", "certo", "correto", "correta"}:
        return "C"
    if normalized in {"e", "errado", "errada", "incorreto", "incorreta"}:
        return "E"
    return None


def _parse_sources(value: str | None) -> list[tuple[str, int, str]]:
    if value is None:
        return []
    parsed: list[tuple[str, int, str]] = []
    pattern = re.compile(
        r"^`?([^`|,;]+?)`?\s*(?:[,|;]\s*)"
        r"(?:p(?:\.|[aá]gina)?\s*)?(\d+)"
        r"(?:\s*[,|;]\s*(.+))?$",
        re.IGNORECASE,
    )
    for raw_line in value.splitlines():
        line = re.sub(r"^\s*[-*+]\s+", "", raw_line).strip()
        match = pattern.match(line)
        if match:
            parsed.append((match.group(1).strip(), int(match.group(2)), (match.group(3) or f"p. {match.group(2)}").strip()))
    return parsed


def _resolve_chunks(
    connection: sqlite3.Connection,
    sources: list[tuple[str, int, str]],
) -> list[tuple[str, str, int, str, str, str]]:
    resolved: list[tuple[str, str, int, str, str, str]] = []
    for relative_path, page, requested_locator in sources:
        row = connection.execute(
            """
            SELECT chunk.id, document.id, chunk.page_start, chunk.locator,
                   chunk.text, chunk.sha256
            FROM source_chunks AS chunk
            JOIN source_documents AS document ON document.id = chunk.document_id
            JOIN source_document_versions AS version ON version.id = chunk.version_id
            WHERE document.relative_path = ?
              AND ? BETWEEN chunk.page_start AND chunk.page_end
              AND version.is_current = 1
              AND chunk.status IN ('usable', 'ocr')
            ORDER BY CASE WHEN lower(chunk.locator) LIKE lower(?) THEN 0 ELSE 1 END,
                     chunk.rowid
            LIMIT 1
            """,
            (relative_path.replace("\\", "/"), page, f"%{requested_locator}%"),
        ).fetchone()
        if row is not None:
            resolved.append((str(row[0]), str(row[1]), int(row[2]), str(row[3]), str(row[4]), str(row[5])))
    return resolved


def _question_blocks(text: str) -> list[tuple[str | None, str]]:
    matches = list(_QUESTION_HEADING.finditer(text))
    return [
        (
            match.group(1).strip() if match.group(1) else None,
            text[match.end() : matches[index + 1].start() if index + 1 < len(matches) else len(text)],
        )
        for index, match in enumerate(matches)
    ]


def _applicability(sections: dict[str, str], discipline: str | None) -> str:
    explicit = _key(_value(sections, "aplicabilidade", "relevância") or "")
    if discipline is None:
        return "style_only"
    if explicit in {"direta", "aplicavel", "aplicável", "applicable"}:
        return "applicable"
    cargo = _key(_value(sections, "cargo") or "")
    return "applicable" if "oficial" in cargo else "style_only"


def _import_questions(
    path: Path,
    text: str,
    connection: sqlite3.Connection,
) -> MarkdownImportSummary:
    applied = needs_review = style_only = 0
    blocks = _question_blocks(text)
    now = datetime.now(UTC).isoformat()
    with transaction(connection):
        for index, (heading_number, body) in enumerate(blocks, start=1):
            sections = _sections(body)
            statement = _value(sections, "enunciado") or ""
            raw_discipline = _value(sections, "disciplina", "matéria de origem")
            discipline = _discipline(raw_discipline)
            answer = _answer(_value(sections, "gabarito"))
            rationale = _value(sections, "fundamentação", "fundamento")
            sources = _parse_sources(_value(sections, "fontes", "fonte"))
            resolved = _resolve_chunks(connection, sources)
            applicability = _applicability(sections, discipline)
            complete = bool(statement and answer and rationale and sources and len(resolved) == len(sources))
            review_status = "verified" if complete else "needs_review"
            if applicability != "applicable":
                style_only += 1
            if not complete:
                needs_review += 1
            raw_id = _value(sections, "id") or heading_number or str(index)
            item_id = "md:" + sha256(
                f"{path.resolve()}|{raw_id}|{statement}".encode("utf-8")
            ).hexdigest()
            metadata = "{}"
            connection.execute(
                """
                INSERT INTO exam_items(
                    id, statement, answer, rationale, discipline_label, exam_name,
                    cargo, board, year, number, metadata_json, review_status,
                    applicability, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    statement = excluded.statement, answer = excluded.answer,
                    rationale = excluded.rationale, discipline_label = excluded.discipline_label,
                    exam_name = excluded.exam_name, cargo = excluded.cargo,
                    board = excluded.board, year = excluded.year, number = excluded.number,
                    review_status = excluded.review_status, applicability = excluded.applicability,
                    content_hash = excluded.content_hash
                """,
                (
                    item_id, statement, answer, rationale, raw_discipline,
                    _value(sections, "prova"), _value(sections, "cargo"),
                    _value(sections, "banca") or "Cebraspe",
                    _integer(_value(sections, "ano")),
                    _integer(_value(sections, "número", "numero")), metadata,
                    review_status, applicability,
                    sha256(statement.encode("utf-8")).hexdigest(),
                ),
            )
            connection.execute("DELETE FROM exam_item_sources WHERE exam_item_id = ?", (item_id,))
            for chunk_id, _document_id, _page, locator, _excerpt, _digest in resolved:
                connection.execute(
                    "INSERT INTO exam_item_sources(exam_item_id, evidence_kind, source_chunk_id, locator) "
                    "VALUES (?, 'local', ?, ?)",
                    (item_id, chunk_id, locator),
                )

            if not (complete and applicability == "applicable" and discipline is not None):
                continue
            question_id = "question:" + item_id[3:]
            concept_key = "markdown:" + sha256(
                f"{discipline}|{statement}|{rationale}".encode("utf-8")
            ).hexdigest()[:20]
            target_id = "target:" + concept_key[9:]
            connection.execute(
                """
                INSERT INTO learning_targets(id, discipline, concept_key, title)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(discipline, concept_key) DO NOTHING
                """,
                (target_id, discipline, concept_key, statement[:160]),
            )
            source_label = "; ".join(f"{source[0]}#p.{source[1]}" for source in sources)
            connection.execute(
                """
                INSERT INTO questions(
                    id, origin, year, cargo, number, discipline, statement, answer,
                    rationale, status, relevance, validated_at, answer_key_source,
                    answer_key_checked_at, delivery_policy, concept_key, content_hash
                ) VALUES (?, 'official', ?, ?, ?, ?, ?, ?, ?, 'validated', 'direct',
                          ?, ?, ?, 'replayable', ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    year = excluded.year, cargo = excluded.cargo, number = excluded.number,
                    discipline = excluded.discipline, statement = excluded.statement,
                    answer = excluded.answer, rationale = excluded.rationale,
                    validated_at = excluded.validated_at,
                    answer_key_source = excluded.answer_key_source,
                    answer_key_checked_at = excluded.answer_key_checked_at,
                    concept_key = excluded.concept_key, content_hash = excluded.content_hash
                """,
                (
                    question_id, _integer(_value(sections, "ano")),
                    _value(sections, "cargo"), _integer(_value(sections, "número", "numero")),
                    discipline, statement, answer, rationale, now, source_label, now,
                    concept_key, sha256(statement.encode("utf-8")).hexdigest(),
                ),
            )
            connection.execute(
                "UPDATE exam_items SET source_question_id = ? WHERE id = ?",
                (question_id, item_id),
            )
            connection.execute("DELETE FROM question_evidence WHERE question_id = ?", (question_id,))
            connection.execute("DELETE FROM question_sources WHERE question_id = ?", (question_id,))
            for evidence_index, (chunk_id, document_id, page, locator, excerpt, digest) in enumerate(resolved, start=1):
                authority = connection.execute(
                    "SELECT authority FROM source_chunks WHERE id = ?", (chunk_id,)
                ).fetchone()[0]
                connection.execute(
                    """
                    INSERT INTO question_evidence(
                        question_id, claim_key, evidence_kind, source_chunk_id,
                        locator, excerpt, evidence_hash, authority
                    ) VALUES (?, ?, 'local', ?, ?, ?, ?, ?)
                    """,
                    (question_id, f"foundation:{evidence_index}", chunk_id, locator, excerpt, digest, authority),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO question_sources(question_id, document_id, page_number, source_role) "
                    "VALUES (?, ?, ?, 'rationale')",
                    (question_id, document_id, page),
                )
            applied += 1
    return MarkdownImportSummary(
        total_questions=len(blocks), applied_questions=applied,
        needs_review=needs_review, style_only=style_only,
    )


def _doctrine_units(text: str) -> list[tuple[str, str]]:
    matches = list(_ANY_HEADING.finditer(text))
    if not matches:
        return [("documento", text.strip())] if text.strip() else []
    units: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end():end].strip()
        units.append((match.group(2).strip(), f"{match.group(0).strip()}\n{body}".strip()))
    return units


def _import_doctrine(
    path: Path,
    root: Path,
    text: str,
    connection: sqlite3.Connection,
) -> MarkdownImportSummary:
    relative_path = path.resolve().relative_to(root.resolve()).as_posix()
    document_id = sha256(relative_path.casefold().encode("utf-8")).hexdigest()
    digest = sha256(path.read_bytes()).hexdigest()
    version_id = f"{document_id}:{digest[:16]}"
    existing = connection.execute(
        "SELECT 1 FROM source_document_versions WHERE id = ?", (version_id,)
    ).fetchone()
    if existing is not None:
        return MarkdownImportSummary(documents_unchanged=1)
    units = _doctrine_units(text)
    discipline = _infer_discipline(relative_path)
    now = datetime.now(UTC).isoformat()
    with transaction(connection):
        connection.execute(
            """
            INSERT INTO source_documents(
                id, relative_path, document_type, sha256, page_count, status,
                quality, processed_at
            ) VALUES (?, ?, 'markdown', ?, 1, 'usable', 1, ?)
            ON CONFLICT(relative_path) DO UPDATE SET
                sha256 = excluded.sha256, page_count = 1, status = 'usable',
                quality = 1, processed_at = excluded.processed_at
            """,
            (document_id, relative_path, digest, now),
        )
        connection.execute(
            "UPDATE source_document_versions SET is_current = 0 WHERE document_id = ?",
            (document_id,),
        )
        connection.execute(
            "UPDATE source_chunks SET status = 'superseded' WHERE document_id = ?",
            (document_id,),
        )
        connection.execute(
            "INSERT INTO source_document_versions(id, document_id, sha256, page_count, status, processed_at, is_current) "
            "VALUES (?, ?, ?, 1, 'usable', ?, 1)",
            (version_id, document_id, digest, now),
        )
        content_hash = sha256(text.encode("utf-8")).hexdigest()
        connection.execute(
            """
            INSERT INTO source_pages(
                document_id, page_number, text, status, page_state,
                extraction_method, content_hash
            ) VALUES (?, 1, ?, 'usable', 'usable', 'text', ?)
            ON CONFLICT(document_id, page_number) DO UPDATE SET
                text = excluded.text, status = 'usable', page_state = 'usable',
                extraction_method = 'text', content_hash = excluded.content_hash,
                quarantine_reason = NULL
            """,
            (document_id, text, content_hash),
        )
        for locator, chunk_text in units:
            chunk_hash = sha256(chunk_text.encode("utf-8")).hexdigest()
            chunk_id = sha256(f"{version_id}|{locator}|{chunk_hash}".encode("utf-8")).hexdigest()
            connection.execute(
                """
                INSERT INTO source_chunks(
                    id, version_id, document_id, page_start, page_end, locator,
                    text, sha256, discipline, source_kind, authority, status
                ) VALUES (?, ?, ?, 1, 1, ?, ?, ?, ?, 'doctrine', 'didactic', 'usable')
                """,
                (chunk_id, version_id, document_id, locator, chunk_text, chunk_hash, discipline),
            )
    # Sem tópico do edital o trecho nunca é elegível para geração; classifica já na importação,
    # como faz "Atualizar acervo", em vez de esperar a próxima atualização.
    enrich_chunk_metadata(connection)
    return MarkdownImportSummary(doctrine_documents=1, doctrine_chunks=len(units))


def import_markdown(
    path: Path,
    project_root: Path,
    connection: sqlite3.Connection,
) -> MarkdownImportSummary:
    """Importa questões ou doutrina sem aceitar formatos manuais alternativos."""

    markdown_path = Path(path)
    if markdown_path.suffix.casefold() != ".md":
        raise ValueError("A entrada manual aceita exclusivamente arquivos Markdown (.md).")
    text = markdown_path.read_text(encoding="utf-8")
    if _QUESTION_HEADING.search(text):
        return _import_questions(markdown_path, text, connection)
    return _import_doctrine(markdown_path, Path(project_root), text, connection)
