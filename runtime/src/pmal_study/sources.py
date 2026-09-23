"""Catálogo imutável e extração rastreável das fontes PDF."""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

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
