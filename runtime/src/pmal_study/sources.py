"""Catálogo imutável e extração rastreável das fontes PDF."""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from pmal_study.topic_mapping import (
    compiled_code_discipline,
    first_article,
    law_topic,
    normalize,
    topic_for_article,
)
from pmal_study.db import transaction

_EXCLUDED_DIRECTORIES = {
    ".git",
    ".pmal-study",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "dist",
    "node_modules",
    "tmp",
}


@dataclass(frozen=True, slots=True)
class CatalogResult:
    cataloged: int
    quarantined: int
    failed: int
    hash_changes: int


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    document_id: str
    page_count: int
    usable_pages: int
    status: str


@dataclass(frozen=True, slots=True)
class SelectiveExtractionResult:
    document_id: str
    requested_pages: int
    usable_pages: int
    failed_pages: int


@dataclass(frozen=True, slots=True)
class CorpusRefreshReport:
    documents_found: int
    documents_processed: int
    documents_unchanged: int
    documents_altered: int
    total_pages: int
    pages_processed: int
    usable_pages: int
    ocr_pages: int
    empty_pages: int
    quarantined_pages: int
    failed_pages: int
    chunks_created: int


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_path(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _document_id(relative_path: str) -> str:
    return sha256(relative_path.casefold().encode("utf-8")).hexdigest()


def _is_source_pdf(path: Path, root: Path) -> bool:
    relative_parts = path.relative_to(root).parts[:-1]
    return not any(part.casefold() in _EXCLUDED_DIRECTORIES for part in relative_parts)


def source_pdf_hashes(root: Path) -> dict[str, str]:
    """Fotografa, em ordem estável, os hashes dos PDFs-fonte do projeto."""

    project_root = Path(root).resolve()
    return {
        _relative_path(path, project_root): _hash_file(path)
        for path in sorted(
            (
                candidate
                for candidate in project_root.rglob("*.pdf")
                if candidate.is_file() and _is_source_pdf(candidate, project_root)
            ),
            key=lambda item: item.as_posix().casefold(),
        )
    }


def _load_overrides(root: Path) -> dict[str, dict[str, Any]]:
    path = root / "config" / "source_overrides.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("source_overrides.json deve conter um objeto JSON.")
    return payload


def _run_process(arguments: list[str], timeout: int = 30) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        arguments,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def _read_page_count(path: Path) -> tuple[int, str | None]:
    try:
        result = _run_process(["pdfinfo", str(path)])
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        return 0, f"pdfinfo indisponível: {error}"
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        return 0, message or "pdfinfo falhou sem diagnóstico"
    match = re.search(rb"(?m)^Pages:\s+(\d+)\s*$", result.stdout)
    if match is None:
        return 0, "pdfinfo não informou a quantidade de páginas"
    return int(match.group(1)), None


def catalog_sources(root: Path, connection: sqlite3.Connection) -> CatalogResult:
    """Cataloga PDFs por caminho e hash sem modificar os arquivos originais."""

    project_root = Path(root).resolve()
    overrides = _load_overrides(project_root)
    cataloged = quarantined = failed = hash_changes = 0
    pdfs = sorted(
        (
            path
            for path in project_root.rglob("*.pdf")
            if path.is_file() and _is_source_pdf(path, project_root)
        ),
        key=lambda item: item.as_posix().casefold(),
    )

    with transaction(connection):
        for path in pdfs:
            relative_path = _relative_path(path, project_root)
            before_hash = _hash_file(path)
            previous = connection.execute(
                "SELECT sha256 FROM source_documents WHERE relative_path = ?",
                (relative_path,),
            ).fetchone()
            if previous is not None and previous[0] != before_hash:
                hash_changes += 1

            page_count, diagnostic = _read_page_count(path)
            override = overrides.get(relative_path) or overrides.get(path.name)
            if override is not None:
                status = str(override.get("status", "ocr_required"))
                diagnostic = str(override.get("reason", diagnostic or "Exceção manual"))
                quarantined += status == "ocr_required"
            elif page_count == 0:
                status = "extraction_failed"
                failed += 1
            else:
                status = "usable"

            after_hash = _hash_file(path)
            if after_hash != before_hash:
                hash_changes += 1
                raise RuntimeError(f"Fonte modificada durante o catálogo: {relative_path}")

            connection.execute(
                """
                INSERT INTO source_documents(
                    id, relative_path, document_type, sha256, page_count,
                    status, quality, extraction_diagnostics, processed_at
                ) VALUES (?, ?, 'pdf', ?, ?, ?, NULL, ?, ?)
                ON CONFLICT(relative_path) DO UPDATE SET
                    sha256 = excluded.sha256,
                    page_count = excluded.page_count,
                    status = excluded.status,
                    quality = NULL,
                    extraction_diagnostics = excluded.extraction_diagnostics,
                    processed_at = excluded.processed_at
                """,
                (
                    _document_id(relative_path),
                    relative_path,
                    before_hash,
                    page_count,
                    status,
                    diagnostic,
                    datetime.now(UTC).isoformat(),
                ),
            )
            cataloged += 1

    return CatalogResult(cataloged, quarantined, failed, hash_changes)


def _normalize_text(raw_text: str) -> str:
    text = unicodedata.normalize("NFC", raw_text.replace("\r\n", "\n").replace("\r", "\n"))
    text = text.replace("\f", "")
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip()


def _quality(text: str) -> tuple[str, float, float]:
    visible = [character for character in text if not character.isspace()]
    if not visible:
        return "ocr_required", 0.0, 0.0
    letter_ratio = sum(character.isalpha() for character in visible) / len(visible)
    replacement_ratio = text.count("\ufffd") / max(len(text), 1)
    letter_count = sum(character.isalpha() for character in visible)
    status = (
        "usable"
        if letter_count >= 12 and letter_ratio >= 0.55 and replacement_ratio <= 0.01
        else "ocr_required"
    )
    return status, letter_ratio, replacement_ratio


def _extract_page(path: Path, page_number: int) -> tuple[str, str, float, float]:
    try:
        result = _run_process(
            [
                "pdftotext",
                "-f",
                str(page_number),
                "-l",
                str(page_number),
                "-enc",
                "UTF-8",
                "-nopgbrk",
                str(path),
                "-",
            ]
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "", "extraction_failed", 0.0, 0.0
    if result.returncode != 0:
        return "", "extraction_failed", 0.0, 0.0
    text = _normalize_text(result.stdout.decode("utf-8", errors="replace"))
    status, letter_ratio, replacement_ratio = _quality(text)
    return text, status, letter_ratio, replacement_ratio


def _extract_all_text_pages(path: Path, page_count: int) -> list[str]:
    try:
        result = _run_process(
            ["pdftotext", "-enc", "UTF-8", str(path), "-"],
            timeout=max(30, min(300, page_count * 2)),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return [""] * page_count
    if result.returncode != 0:
        return [""] * page_count
    raw_pages = result.stdout.decode("utf-8", errors="replace").split("\f")
    if len(raw_pages) > page_count and not raw_pages[-1].strip():
        raw_pages.pop()
    pages = [_normalize_text(page) for page in raw_pages[:page_count]]
    return pages + [""] * (page_count - len(pages))


def _find_tesseract() -> str | None:
    executable = shutil.which("tesseract")
    if executable:
        return executable
    candidates = (
        Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
        Path("C:/Program Files (x86)/Tesseract-OCR/tesseract.exe"),
        Path.home() / "AppData/Local/Programs/Tesseract-OCR/tesseract.exe",
    )
    return next((str(path) for path in candidates if path.is_file()), None)


def _ocr_page(
    path: Path,
    page_number: int,
    *,
    tesseract_path: str | None = None,
    tessdata_dir: Path | None = None,
) -> str:
    executable = tesseract_path or _find_tesseract()
    if executable is None:
        return ""
    with tempfile.TemporaryDirectory(prefix="pmal-ocr-") as temporary:
        output_base = Path(temporary) / "page"
        try:
            render = _run_process(
                [
                    "pdftoppm", "-f", str(page_number), "-l", str(page_number),
                    "-r", "300", "-png", "-singlefile", str(path), str(output_base),
                ],
                timeout=120,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return ""
        image_path = output_base.with_suffix(".png")
        if render.returncode != 0 or not image_path.is_file():
            return ""
        arguments = [executable, str(image_path), "stdout"]
        if tessdata_dir is not None:
            arguments.extend(["--tessdata-dir", str(tessdata_dir)])
        arguments.extend(["-l", "por" if tessdata_dir is not None else "eng", "--psm", "6"])
        try:
            result = _run_process(arguments, timeout=120)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return ""
        if result.returncode != 0:
            return ""
        return _normalize_text(result.stdout.decode("utf-8", errors="replace"))


_STRUCTURAL_LINE = re.compile(
    r"(?i)^(?:art\.?\s*\d+[ºo]?|§\s*\d+|[IVXLCDM]+\s*[-–—.]|"
    r"(?:t[ií]tulo|cap[ií]tulo|se[cç][aã]o|livro|parte)\b)"
)


def _chunk_page(text: str, page_number: int, limit: int = 1800) -> list[tuple[str, str]]:
    if not text.strip():
        return []
    blocks: list[list[str]] = []
    current: list[str] = []
    current_size = 0
    for line in (line.strip() for line in text.splitlines() if line.strip()):
        starts_unit = bool(_STRUCTURAL_LINE.match(line))
        if current and (starts_unit or current_size + len(line) + 1 > limit):
            blocks.append(current)
            current = []
            current_size = 0
        current.append(line)
        current_size += len(line) + 1
    if current:
        blocks.append(current)
    chunks: list[tuple[str, str]] = []
    for index, lines in enumerate(blocks, start=1):
        chunk_text = "\n".join(lines)
        first = lines[0][:120]
        locator = first if _STRUCTURAL_LINE.match(first) else f"p. {page_number}, bloco {index}"
        chunks.append((locator, chunk_text))
    return chunks


def _infer_discipline(relative_path: str) -> str | None:
    normalized = normalize(relative_path)
    compiled = compiled_code_discipline(relative_path)
    if compiled:
        return compiled
    if "cppm" in normalized or "processo penal militar" in normalized:
        return "direito_processual_penal_militar"
    if re.search(r"(?:^|/)cpm(?:/|\s|-)", normalized) or "penal militar" in normalized:
        return "direito_penal_militar"
    if "legislacao" in normalized or law_topic(relative_path):
        return "legislacao_pmal"
    if "conhecimentos al" in normalized or "alagoas" in normalized:
        return "conhecimentos_alagoas"
    return None


_OFFICIAL_FOLDERS = ("legislacao oficial/", "leis oficiais/", "oficial/")


def _infer_authority(relative_path: str) -> str:
    """Só é cópia oficial o texto normativo íntegro; apostilas comentadas são didáticas."""

    normalized = normalize(relative_path)
    filename = Path(normalized).name
    if compiled_code_discipline(relative_path):
        return "local_official_copy"
    if any(folder in f"/{normalized}" for folder in (f"/{name}" for name in _OFFICIAL_FOLDERS)):
        return "local_official_copy"
    if "edital" in filename:
        return "exam_board"
    return "didactic"


_TOPIC_STOPWORDS = {
    "das", "dos", "uma", "para", "pela", "pelo", "sobre", "como", "militar",
    "militares", "direito", "estado", "alagoas", "policia", "processo", "penal",
}


def _topic_terms(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    normalized = "".join(character for character in normalized if not unicodedata.combining(character))
    return {
        token for token in re.findall(r"[a-z0-9]{3,}", normalized)
        if token not in _TOPIC_STOPWORDS
    }


def _structural_topics(
    rows: list[tuple[Any, ...]],
) -> dict[str, tuple[str | None, str | None]]:
    """Tópico por faixa de artigos (códigos compilados) ou por número da lei.

    Trechos sem marcador de artigo herdam o tópico do trecho anterior do mesmo documento.
    """

    assigned: dict[str, tuple[str | None, str | None]] = {}
    current: dict[str, str | None] = {}
    for chunk_id, _discipline, _locator, text, relative_path, document_id in rows:
        compiled = compiled_code_discipline(str(relative_path))
        if compiled:
            article = first_article(str(text))
            if article is not None:
                found, topic = topic_for_article(compiled, article)
                if found:
                    current[str(document_id)] = topic
            if str(document_id) in current:
                assigned[str(chunk_id)] = (compiled, current[str(document_id)])
            continue
        topic = law_topic(str(relative_path))
        if topic and _infer_authority(str(relative_path)) == "local_official_copy":
            assigned[str(chunk_id)] = ("legislacao_pmal", topic)
    return assigned


def enrich_chunk_metadata(connection: sqlite3.Connection) -> int:
    """Classifica disciplina, autoridade e tópico de forma auditável.

    Códigos compilados e leis oficiais usam a estrutura normativa (artigos, número da lei);
    o restante usa sobreposição lexical com os títulos do edital.
    """

    topics: dict[str, list[tuple[str, str, set[str]]]] = {}
    for topic_id, discipline, title in connection.execute(
        "SELECT id, discipline, title FROM syllabus_topics"
    ):
        terms = _topic_terms(title)
        # Tópicos-raiz (ex.: "dppm") funcionariam como depósito genérico da disciplina.
        if terms and "." in str(topic_id):
            topics.setdefault(discipline, []).append((topic_id, title, terms))
    rows = connection.execute(
        """
        SELECT chunk.id, chunk.discipline, chunk.locator, chunk.text,
               document.relative_path, document.id
        FROM source_chunks AS chunk
        JOIN source_document_versions AS version ON version.id = chunk.version_id
        JOIN source_documents AS document ON document.id = chunk.document_id
        WHERE version.is_current = 1 AND chunk.status IN ('usable', 'ocr')
        ORDER BY document.id, chunk.page_start, chunk.rowid
        """
    ).fetchall()
    structural = _structural_topics(rows)
    updates: list[tuple[str | None, str, str | None, str]] = []
    for chunk_id, _stored, locator, text, relative_path, _document_id in rows:
        authority = _infer_authority(str(relative_path))
        if str(chunk_id) in structural:
            discipline, selected_topic = structural[str(chunk_id)]
            updates.append((discipline, authority, selected_topic, str(chunk_id)))
            continue
        discipline = _infer_discipline(str(relative_path))
        selected_topic: str | None = None
        if discipline in topics:
            haystack = _topic_terms(f"{locator} {str(text)[:3000]}")
            candidates: list[tuple[float, str]] = []
            normalized_text = normalize(f"{locator} {str(text)[:3000]}")
            for topic_id, title, terms in topics[discipline]:
                overlap = len(terms & haystack)
                if overlap == 0:
                    continue
                phrase = normalize(title)
                score = overlap / len(terms) + (2 if phrase in normalized_text else 0)
                candidates.append((score, topic_id))
            if candidates:
                selected_topic = max(candidates)[1]
        updates.append((discipline, authority, selected_topic, str(chunk_id)))
    with transaction(connection):
        connection.executemany(
            "UPDATE source_chunks SET discipline = ?, authority = ?, topic_id = ? WHERE id = ?",
            updates,
        )
    return len(updates)


def _version_id(document_id: str, digest: str) -> str:
    return f"{document_id}:{digest[:16]}"


def refresh_corpus(
    root: Path,
    connection: sqlite3.Connection,
    *,
    tesseract_path: str | None = None,
    tessdata_dir: Path | None = None,
) -> CorpusRefreshReport:
    """Atualiza incrementalmente páginas e fragmentos, preservando versões anteriores."""

    project_root = Path(root).resolve()
    catalog_sources(project_root, connection)
    documents = connection.execute(
        "SELECT id, relative_path, sha256, page_count, status FROM source_documents "
        "ORDER BY relative_path"
    ).fetchall()
    processed = unchanged = altered = pages_processed = 0
    usable_pages = ocr_pages = empty_pages = quarantined_pages = failed_pages = 0
    chunks_created = 0

    for document_id, relative_path, digest, page_count, catalog_status in documents:
        version_id = _version_id(document_id, digest)
        known_versions = connection.execute(
            "SELECT id, sha256 FROM source_document_versions WHERE document_id = ?",
            (document_id,),
        ).fetchall()
        page_rows = connection.execute(
            "SELECT count(*), sum(content_hash IS NOT NULL), "
            "sum(page_state IN ('usable', 'ocr')), sum(page_state = 'quarantined') "
            "FROM source_pages WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        complete = page_rows[0] == page_count and (page_rows[1] or 0) == page_count
        current_known = any(row[0] == version_id for row in known_versions)
        indexed_chunks = connection.execute(
            "SELECT count(*) FROM source_chunks WHERE version_id = ? AND status <> 'superseded'",
            (version_id,),
        ).fetchone()[0]
        indexed = indexed_chunks > 0 or (page_rows[2] or 0) == 0
        if current_known and complete and indexed:
            quality = (page_rows[2] or 0) / max(page_count, 1)
            connection.execute(
                "UPDATE source_documents SET status = ?, quality = ? WHERE id = ?",
                (
                    "ocr_required" if (page_rows[3] or 0) else "usable",
                    quality,
                    document_id,
                ),
            )
            unchanged += 1
            continue
        if known_versions and not current_known:
            altered += 1

        source_path = project_root / relative_path
        force_ocr = catalog_status == "ocr_required"
        text_pages = [""] * page_count if force_ocr else _extract_all_text_pages(source_path, page_count)
        page_records: list[tuple[Any, ...]] = []
        chunk_records: list[tuple[Any, ...]] = []
        document_usable_pages = 0
        discipline = _infer_discipline(relative_path)
        for page_number in range(1, page_count + 1):
            text = text_pages[page_number - 1]
            text_status, letter_ratio, replacement_ratio = _quality(text)
            extraction_method = "text"
            page_state = "usable" if text_status == "usable" else "empty"
            quarantine_reason = None
            if text_status != "usable":
                ocr_text = _ocr_page(
                    source_path,
                    page_number,
                    tesseract_path=tesseract_path,
                    tessdata_dir=tessdata_dir,
                )
                ocr_status, letter_ratio, replacement_ratio = _quality(ocr_text)
                extraction_method = "ocr"
                if ocr_status == "usable":
                    text = ocr_text
                    page_state = "ocr"
                    ocr_pages += 1
                    document_usable_pages += 1
                else:
                    text = ocr_text
                    if not ocr_text.strip():
                        page_state = "empty"
                        quarantine_reason = "Página sem conteúdo textual detectável"
                        empty_pages += 1
                    else:
                        page_state = "quarantined"
                        quarantine_reason = "OCR sem texto utilizável"
                        quarantined_pages += 1
            else:
                usable_pages += 1
                document_usable_pages += 1
            legacy_status = (
                "usable" if page_state in {"usable", "ocr"}
                else "ocr_required" if page_state in {"empty", "quarantined"}
                else "extraction_failed"
            )
            content_hash = sha256(text.encode("utf-8")).hexdigest()
            page_records.append(
                (
                    document_id, page_number, text, legacy_status, letter_ratio,
                    replacement_ratio, page_state, extraction_method, content_hash,
                    quarantine_reason,
                )
            )
            for locator, chunk_text in _chunk_page(text, page_number):
                chunk_hash = sha256(chunk_text.encode("utf-8")).hexdigest()
                chunk_id = sha256(
                    f"{version_id}|{page_number}|{locator}|{chunk_hash}".encode("utf-8")
                ).hexdigest()
                chunk_records.append(
                    (
                        chunk_id, version_id, document_id, page_number, page_number,
                        locator, chunk_text, chunk_hash, discipline, _infer_authority(relative_path),
                        "ocr" if page_state == "ocr" else "usable",
                    )
                )

        with transaction(connection):
            connection.execute(
                "UPDATE source_document_versions SET is_current = 0 WHERE document_id = ?",
                (document_id,),
            )
            connection.execute(
                "UPDATE source_chunks SET status = 'superseded' WHERE document_id = ? "
                "AND version_id <> ? AND status <> 'superseded'",
                (document_id, version_id),
            )
            connection.execute(
                """
                INSERT INTO source_document_versions(
                    id, document_id, sha256, page_count, status, processed_at,
                    is_current, extraction_diagnostics
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(document_id, sha256) DO UPDATE SET
                    is_current = 1,
                    processed_at = excluded.processed_at,
                    status = excluded.status,
                    extraction_diagnostics = excluded.extraction_diagnostics
                """,
                (
                    version_id, document_id, digest, page_count,
                    "quarantined" if any(row[6] == "quarantined" for row in page_records) else "usable",
                    datetime.now(UTC).isoformat(),
                    "Há páginas em quarentena" if any(row[6] == "quarantined" for row in page_records) else None,
                ),
            )
            connection.executemany(
                """
                INSERT INTO source_pages(
                    document_id, page_number, text, status, letter_ratio,
                    replacement_ratio, page_state, extraction_method, content_hash,
                    quarantine_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_id, page_number) DO UPDATE SET
                    text = excluded.text,
                    status = excluded.status,
                    letter_ratio = excluded.letter_ratio,
                    replacement_ratio = excluded.replacement_ratio,
                    page_state = excluded.page_state,
                    extraction_method = excluded.extraction_method,
                    content_hash = excluded.content_hash,
                    quarantine_reason = excluded.quarantine_reason
                """,
                page_records,
            )
            connection.executemany(
                """
                INSERT INTO source_chunks(
                    id, version_id, document_id, page_start, page_end, locator,
                    text, sha256, discipline, authority, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET status = excluded.status
                """,
                chunk_records,
            )
            connection.execute(
                "UPDATE source_documents SET status = ?, quality = ?, processed_at = ? WHERE id = ?",
                (
                    "ocr_required" if any(row[6] == "quarantined" for row in page_records) else "usable",
                    document_usable_pages / max(page_count, 1),
                    datetime.now(UTC).isoformat(), document_id,
                ),
            )
        processed += 1
        pages_processed += page_count
        chunks_created += len(chunk_records)

    enrich_chunk_metadata(connection)
    totals = connection.execute(
        """
        SELECT count(*),
               sum(page_state = 'usable'), sum(page_state = 'ocr'),
               sum(page_state = 'empty'), sum(page_state = 'quarantined'),
               sum(page_state = 'extraction_failed')
        FROM source_pages
        """
    ).fetchone()
    return CorpusRefreshReport(
        documents_found=len(documents),
        documents_processed=processed,
        documents_unchanged=unchanged,
        documents_altered=altered,
        total_pages=totals[0] or 0,
        pages_processed=pages_processed,
        usable_pages=totals[1] or 0,
        ocr_pages=totals[2] or 0,
        empty_pages=totals[3] or 0,
        quarantined_pages=totals[4] or 0,
        failed_pages=totals[5] or 0,
        chunks_created=chunks_created,
    )


def extract_document(
    document_id: str,
    connection: sqlite3.Connection,
    project_root: Path | None = None,
) -> ExtractionResult:
    """Extrai e indexa, página a página, um documento já catalogado."""

    row = connection.execute(
        "SELECT relative_path, page_count, status FROM source_documents WHERE id = ?",
        (document_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Documento não catalogado: {document_id}")
    relative_path, page_count, current_status = row
    if current_status == "ocr_required":
        return ExtractionResult(document_id, page_count, 0, "ocr_required")
    if page_count <= 0:
        return ExtractionResult(document_id, page_count, 0, "extraction_failed")

    root = Path.cwd() if project_root is None else Path(project_root)
    source_path = root.resolve() / relative_path
    pages: list[tuple[int, str, str, float, float]] = []
    for page_number in range(1, page_count + 1):
        text, status, letter_ratio, replacement_ratio = _extract_page(
            source_path, page_number
        )
        pages.append(
            (page_number, text, status, letter_ratio, replacement_ratio)
        )

    usable_pages = sum(page[2] == "usable" for page in pages)
    failed_pages = sum(page[2] == "extraction_failed" for page in pages)
    quality = usable_pages / page_count
    if failed_pages == page_count:
        document_status = "extraction_failed"
    elif quality >= 0.80:
        document_status = "usable"
    else:
        document_status = "ocr_required"

    with transaction(connection):
        connection.execute(
            "DELETE FROM source_pages WHERE document_id = ?", (document_id,)
        )
        connection.executemany(
            """
            INSERT INTO source_pages(
                document_id, page_number, text, status,
                letter_ratio, replacement_ratio
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                (document_id, page_number, text, status, letter_ratio, replacement_ratio)
                for page_number, text, status, letter_ratio, replacement_ratio in pages
            ),
        )
        connection.execute(
            """
            UPDATE source_documents
            SET status = ?, quality = ?, processed_at = ?
            WHERE id = ?
            """,
            (document_status, quality, datetime.now(UTC).isoformat(), document_id),
        )

    return ExtractionResult(document_id, page_count, usable_pages, document_status)


def extract_document_pages(
    document_id: str,
    page_numbers: list[int] | set[int] | tuple[int, ...],
    connection: sqlite3.Connection,
    project_root: Path | None = None,
) -> SelectiveExtractionResult:
    """Extrai apenas páginas citadas, preservando as demais páginas já indexadas."""

    row = connection.execute(
        "SELECT relative_path, page_count, status FROM source_documents WHERE id = ?",
        (document_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Documento não catalogado: {document_id}")
    relative_path, page_count, document_status = row
    requested = sorted(set(page_numbers))
    if any(not isinstance(page, int) or page < 1 or page > page_count for page in requested):
        raise ValueError(
            f"Página fora do intervalo 1..{page_count} em {relative_path}."
        )
    if document_status == "ocr_required":
        return SelectiveExtractionResult(document_id, len(requested), 0, len(requested))

    root = Path.cwd() if project_root is None else Path(project_root)
    source_path = root.resolve() / relative_path
    pages = [
        (page, *_extract_page(source_path, page))
        for page in requested
    ]
    with transaction(connection):
        connection.executemany(
            """
            INSERT INTO source_pages(
                document_id, page_number, text, status,
                letter_ratio, replacement_ratio
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id, page_number) DO UPDATE SET
                text = excluded.text,
                status = excluded.status,
                letter_ratio = excluded.letter_ratio,
                replacement_ratio = excluded.replacement_ratio
            """,
            (
                (document_id, page, text, status, letter_ratio, replacement_ratio)
                for page, text, status, letter_ratio, replacement_ratio in pages
            ),
        )
    return SelectiveExtractionResult(
        document_id=document_id,
        requested_pages=len(requested),
        usable_pages=sum(page[2] == "usable" for page in pages),
        failed_pages=sum(page[2] != "usable" for page in pages),
    )
