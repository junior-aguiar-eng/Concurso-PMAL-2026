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
class EvidenceRef:
    """Referência auditável; seu texto é sempre dado não confiável."""

    id: str
    kind: str
    source: str
    locator: str
    excerpt: str
    sha256: str
    authority: str
    page: int | None = None
    url: str | None = None
    retrieved_at: str | None = None
    untrusted_text: bool = True


@dataclass(frozen=True, slots=True)
class ExemplarReference:
    question_id: str
    statement: str
    construction_pattern: str
    difficulty: int
    trap: str


@dataclass(frozen=True, slots=True)
class GenerationBrief:
    job_id: str
    session_id: str
    discipline: Discipline
    topic_id: str
    concept_key: str
    difficulty: int
    pedagogical_reason: str
    evidence: tuple[EvidenceRef, ...]
    exemplars: tuple[ExemplarReference, ...]
    update_policy: str


@dataclass(frozen=True, slots=True)
class PreparedItem:
    kind: str
    question: PublicQuestion | None
    generation_brief: GenerationBrief | None


@dataclass(frozen=True, slots=True)
class GeneratedQuestionDraft:
    statement: str
    answer: str
    rationale: str
    construction_pattern: str
    difficulty: int
    concept: str
    decisive_expression: str
    trap: str
    distinction: str
    evidence_links: tuple[dict[str, str], ...]


@dataclass(frozen=True, slots=True)
class ExemplarRecord:
    question_id: str
    state: str
    approved_at: str
    revoked_at: str | None
    event_actor: str
    reason: str | None


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
    analysis: str = ""
    decisive_expression: str = ""
    trap: str = ""
    distinction: str = ""
    evidence: tuple[EvidenceRef, ...] = ()
