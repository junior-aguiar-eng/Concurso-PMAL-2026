"""Consulta rastreável a fontes jurídicas oficiais permitidas."""

from __future__ import annotations

import hashlib
import sqlite3
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


def _source_kind(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme != "https":
        raise SourcePolicyError("Somente HTTPS é permitido para fonte em tempo real.")
    domains = {
        "planalto": "planalto.gov.br",
        "stf": "stf.jus.br",
        "stj": "stj.jus.br",
    }
    for kind, domain in domains.items():
        if host == domain or host.endswith(f".{domain}"):
            return kind
    raise SourcePolicyError("Domínio fora da lista oficial Planalto/STF/STJ.")


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
