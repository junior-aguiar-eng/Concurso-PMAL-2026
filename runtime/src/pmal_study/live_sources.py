"""Consulta rastreável a fontes jurídicas oficiais permitidas."""

from __future__ import annotations

import hashlib
import html
import re
import sqlite3
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from pmal_study.db import transaction


_MAX_BYTES = 2 * 1024 * 1024


class SourcePolicyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SourceCheckResult:
    id: str
    url: str
    source_kind: str
    retrieved_at: str
    sha256: str
    status: str
    citation: str
    bytes_read: int


@dataclass(frozen=True, slots=True)
class LiveEvidenceResult:
    id: str
    url: str
    source_kind: str
    author: str | None
    locator: str
    excerpt: str
    retrieved_at: str
    sha256: str
    authority: str
    status: str
    previous_snapshot_id: str | None
    untrusted_text: bool = True


def _source_kind(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme != "https":
        raise SourcePolicyError("Somente HTTPS é permitido para fonte em tempo real.")
    domains = {
        "planalto": "planalto.gov.br",
        "stf": "stf.jus.br",
        "stj": "stj.jus.br",
        "cebraspe": "cebraspe.org.br",
        "pmal": "pm.al.gov.br",
        # Abrange secretarias e autarquias estaduais (ex.: itec.al.gov.br) e municípios.
        "alagoas_governo": "al.gov.br",
        "aleal": "al.al.leg.br",
        "tjal": "tjal.jus.br",
        "ibge": "ibge.gov.br",
        "ufal": "ufal.br",
    }
    for kind, domain in domains.items():
        if host == domain or host.endswith(f".{domain}"):
            return kind
    if host.endswith(".edu.br") or host == "edu.br":
        return "academic"
    raise SourcePolicyError("Domínio fora da lista institucional permitida.")


def _authority(kind: str) -> str:
    if kind == "planalto":
        return "official_legislation"
    if kind in {"stf", "stj", "tjal"}:
        return "official_court"
    if kind == "cebraspe":
        return "exam_board"
    if kind in {"ufal", "academic"}:
        return "academic"
    return "official_institution"


def _comparable_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", html.unescape(value).casefold())
    normalized = "".join(character for character in normalized if not unicodedata.combining(character))
    return " ".join(re.sub(r"<[^>]+>", " ", normalized).split())


def register_live_evidence(
    url: str,
    locator: str,
    excerpt: str,
    connection: sqlite3.Connection,
    *,
    author: str | None = None,
    fetcher: Callable[[str], tuple[str, bytes]] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> LiveEvidenceResult:
    """Confirma que o trecho existe e congela um snapshot auditável."""

    kind = _source_kind(url)
    if not locator.strip() or len(_comparable_text(excerpt)) < 8:
        raise SourcePolicyError("Localizador e trecho confirmado são obrigatórios.")
    try:
        final_url, body = (fetcher or _fetch)(url)
    except SourcePolicyError:
        raise
    except OSError as error:
        raise SourcePolicyError(f"Falha ao consultar a fonte oficial: {error}") from error
    final_kind = _source_kind(final_url)
    if final_kind != kind:
        raise SourcePolicyError("O redirecionamento saiu do domínio institucional esperado.")
    body_text = body.decode("utf-8", errors="replace")
    if _comparable_text(excerpt) not in _comparable_text(body_text):
        raise SourcePolicyError("O trecho informado não foi localizado na fonte recuperada.")
    digest = hashlib.sha256(body).hexdigest()
    previous = connection.execute(
        """
        SELECT id, sha256 FROM live_source_snapshots
        WHERE final_url = ? AND locator = ?
        ORDER BY retrieved_at DESC LIMIT 1
        """,
        (final_url, locator.strip()),
    ).fetchone()
    status = "changed" if previous is not None and previous[1] != digest else "verified"
    now = (clock or (lambda: datetime.now(UTC)))()
    retrieved_at = (now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)).isoformat()
    identifier = str(uuid.uuid4())
    previous_id = str(previous[0]) if previous is not None else None
    with transaction(connection):
        connection.execute(
            """
            INSERT INTO live_source_snapshots(
                id, url, final_url, source_kind, author, locator,
                confirmed_excerpt, sha256, retrieved_at, authority,
                previous_snapshot_id, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                identifier, url, final_url, kind, author, locator.strip(), excerpt.strip(),
                digest, retrieved_at, _authority(kind), previous_id, status,
            ),
        )
    return LiveEvidenceResult(
        id=identifier, url=final_url, source_kind=kind, author=author,
        locator=locator.strip(), excerpt=excerpt.strip(), retrieved_at=retrieved_at,
        sha256=digest, authority=_authority(kind), status=status,
        previous_snapshot_id=previous_id,
    )


def _fetch(url: str) -> tuple[str, bytes]:
    request = Request(url, headers={"User-Agent": "Treinador-PMAL-Oficial/0.1"})
    with urlopen(request, timeout=10) as response:  # noqa: S310 - allowlist above
        body = response.read(_MAX_BYTES + 1)
        if len(body) > _MAX_BYTES:
            raise SourcePolicyError("Resposta oficial excede o limite de 2 MiB.")
        return response.geturl(), body


def verify_official_source(
    url: str,
    citation: str,
    connection: sqlite3.Connection,
    *,
    fetcher: Callable[[str], tuple[str, bytes]] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> SourceCheckResult:
    """Busca fonte permitida, compara o hash anterior e registra a verificação."""

    kind = _source_kind(url)
    if not isinstance(citation, str) or not citation.strip():
        raise SourcePolicyError("A citação jurídica ou temática é obrigatória.")
    try:
        final_url, body = (fetcher or _fetch)(url)
    except SourcePolicyError:
        raise
    except OSError as error:
        raise SourcePolicyError(f"Falha ao consultar a fonte oficial: {error}") from error
    try:
        final_kind = _source_kind(final_url)
    except SourcePolicyError as error:
        raise SourcePolicyError(
            "O redirecionamento saiu do domínio oficial esperado."
        ) from error
    if final_kind != kind:
        raise SourcePolicyError("O redirecionamento saiu do domínio oficial esperado.")
    digest = hashlib.sha256(body).hexdigest()
    previous = connection.execute(
        "SELECT sha256 FROM source_checks WHERE url = ? ORDER BY retrieved_at DESC LIMIT 1",
        (url,),
    ).fetchone()
    status = "changed" if previous is not None and previous[0] != digest else "verified"
    now = (clock or (lambda: datetime.now(UTC)))()
    retrieved_at = (
        now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    ).isoformat()
    identifier = str(uuid.uuid4())
    with transaction(connection):
        connection.execute(
            """
            INSERT INTO source_checks(
                id, url, source_kind, retrieved_at, sha256, status, citation
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                identifier,
                url,
                kind,
                retrieved_at,
                digest,
                status,
                citation.strip(),
            ),
        )
    return SourceCheckResult(
        id=identifier,
        url=url,
        source_kind=kind,
        retrieved_at=retrieved_at,
        sha256=digest,
        status=status,
        citation=citation.strip(),
        bytes_read=len(body),
    )
