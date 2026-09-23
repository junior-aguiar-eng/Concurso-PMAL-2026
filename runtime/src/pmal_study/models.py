"""Tipos imutáveis e limites do domínio de estudo."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Discipline(StrEnum):
    """Disciplinas admitidas no recorte de Oficial da PMAL."""

    DIREITO_PENAL_MILITAR = "direito_penal_militar"
    DIREITO_PROCESSUAL_PENAL_MILITAR = "direito_processual_penal_militar"
    LEGISLACAO_PMAL = "legislacao_pmal"
    CONHECIMENTOS_ALAGOAS = "conhecimentos_alagoas"


class QuestionStatus(StrEnum):
    """Estados editoriais possíveis para uma questão."""

    VALIDATED = "validated"
    NEEDS_ANSWER_KEY = "needs_answer_key"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


class SourceStatus(StrEnum):
    """Resultados possíveis da avaliação de uma fonte documental."""

    USABLE = "usable"
    OCR_REQUIRED = "ocr_required"
    EXTRACTION_FAILED = "extraction_failed"


@dataclass(frozen=True, slots=True)
class PublicQuestion:
    """Representação segura de uma questão antes da submissão da resposta."""

    id: str
    discipline: Discipline
    topic_id: str
    statement: str
    origin_label: str


@dataclass(frozen=True, slots=True)
class GradingResult:
    """Resultado completo liberado somente após a submissão da resposta."""

    correct: bool
    expected_answer: str
    rationale: str
    source_label: str
    source_page: int
    next_review_at: datetime
    error_pattern: str | None
