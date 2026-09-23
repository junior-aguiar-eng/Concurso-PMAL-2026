"""Análise empírica e reproduzível do estilo das provas Cebraspe."""

from __future__ import annotations

import math
import re
import statistics
import subprocess
from hashlib import sha256
from pathlib import Path
from typing import Any

_ITEM_START = re.compile(r"(?m)^\s*(\d{1,3})[ \t]+(?=\S)")
_TRAILING_COMMAND = re.compile(
    r"(?<=[.!?])\s+(?:Acerca|A respeito|Com relação|Julgue|Em cada|No que|Tendo|Considerando)\b",
    re.IGNORECASE,
)
_ABSOLUTE_TERMS = (
    "não",
    "sempre",
    "somente",
    "apenas",
    "qualquer",
    "exclusivamente",
    "necessariamente",
    "vedado",
    "vedada",
    "em nenhuma hipótese",
)


def _read_pdf_raw(path: Path) -> str:
    result = subprocess.run(
        ["pdftotext", "-raw", "-enc", "UTF-8", str(path), "-"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    if result.returncode != 0:
        diagnostic = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Falha ao extrair {path}: {diagnostic}")
    return result.stdout.decode("utf-8", errors="replace")


def _clean_item(text: str) -> str:
    text = text.replace("\f", "\n")
    text = re.sub(r"(?m)^.*CESPE\s*\|\s*CEBRASPE.*$", "", text)
    text = re.sub(r"(?m)^\s*Espaço livre\s*$", "", text, flags=re.IGNORECASE)
    trailing = _TRAILING_COMMAND.search(text)
    if trailing is not None:
        text = text[: trailing.start()]
    return re.sub(r"\s+", " ", text).strip()


def extract_numbered_items(
    path: Path, first: int = 1, last: int = 120
) -> dict[int, str]:
    """Recupera itens na ordem numérica usando o último início antes do item seguinte."""

    text = _read_pdf_raw(Path(path))
    candidates: dict[int, list[re.Match[str]]] = {}
    for match in _ITEM_START.finditer(text):
        number = int(match.group(1))
        if first <= number <= last:
            candidates.setdefault(number, []).append(match)

    selected: dict[int, re.Match[str]] = {}
    upper_bound = len(text) + 1
    for number in range(last, first - 1, -1):
        eligible = [
            match for match in candidates.get(number, []) if match.start() < upper_bound
        ]
        if not eligible:
            raise ValueError(f"Item {number} não localizado em {path.name}.")
        selected[number] = eligible[-1]
        upper_bound = eligible[-1].start()

    items: dict[int, str] = {}
    for number in range(first, last + 1):
        start = selected[number].end()
        end = selected[number + 1].start() if number < last else len(text)
        cleaned = _clean_item(text[start:end])
        if not cleaned:
            raise ValueError(f"Item {number} foi extraído sem texto de {path.name}.")
        items[number] = cleaned
    return items


def _percentile(values: list[int], fraction: float) -> int:
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * fraction) - 1)
    return ordered[index]


def analyze_exam_style(paths: list[Path]) -> dict[str, Any]:
    """Produz métricas estilísticas sem converter matérias externas em conteúdo."""

    all_items: list[str] = []
    years: set[int] = set()
    sources: list[dict[str, Any]] = []
    for raw_path in paths:
        path = Path(raw_path)
        items = extract_numbered_items(path)
        all_items.extend(items.values())
        year_match = re.search(r"20\d{2}", path.name)
        if year_match is not None:
            years.add(int(year_match.group(0)))
        sources.append(
            {
                "document": path.name,
                "sha256": sha256(path.read_bytes()).hexdigest(),
                "question_count": len(items),
            }
        )

    word_counts = [len(re.findall(r"\b\w+\b", item, flags=re.UNICODE)) for item in all_items]
    casefolded = [item.casefold() for item in all_items]
    absolute_counts = {
        term: sum(len(re.findall(rf"(?<!\w){re.escape(term)}(?!\w)", item)) for item in casefolded)
        for term in _ABSOLUTE_TERMS
    }
    return {
        "schema_version": 1,
        "sample": {
            "exam_count": len(paths),
            "question_count": len(all_items),
            "years": sorted(years),
        },
        "sources": sources,
        "patterns": {
            "hypothetical_situation_count": sum(
                "situação hipotética" in item for item in casefolded
            ),
            "assertion_marker_count": sum("assertiva:" in item for item in casefolded),
            "absolute_term_counts": absolute_counts,
            "word_count": {
                "mean": round(statistics.fmean(word_counts), 2),
                "median": int(statistics.median(word_counts)),
                "p90": _percentile(word_counts, 0.90),
            },
            "long_item_count_50_plus_words": sum(count >= 50 for count in word_counts),
        },
        "scope_policy": {
            "style_corpus": "todas_as_questoes_cebraspe_das_provas_analisadas",
            "study_corpus": [
                "direito_penal_militar",
                "direito_processual_penal_militar",
                "legislacao_pmal",
                "conhecimentos_alagoas",
            ],
            "cross_discipline_frequency_affects_subject_weight": False,
        },
    }
