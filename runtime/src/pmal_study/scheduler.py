"""Priorização determinística e revisão espaçada do estudo."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Sequence

from pmal_study.models import Discipline


@dataclass(frozen=True, slots=True)
class SessionFilters:
    disciplines: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        invalid = set(self.disciplines) - {item.value for item in Discipline}
        if invalid:
            raise ValueError(f"Disciplinas fora do escopo: {sorted(invalid)}")


@dataclass(frozen=True, slots=True)
class RankedQuestion:
    question_id: str
    topic_id: str
    discipline: str
    priority: float
    reason: str


@dataclass(frozen=True, slots=True)
class PriorAttempt:
    correct: bool
    confidence: int


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    next_review_at: datetime
    interval_days: int
    consecutive_confident_correct: int


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    return _aware(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _clamp(value: float) -> float:
    return max(0.0, min(value, 1.0))


def _reason(
    overdue_score: float,
    weakness_score: float,
    unseen: bool,
    historical_score: float,
) -> str:
    if overdue_score > 0 and weakness_score >= 0.5:
        return "overdue_and_weak"
    if overdue_score > 0:
        return "overdue"
    if unseen and historical_score > 0:
        return "unseen_high_frequency"
    if unseen:
        return "unseen"
    if weakness_score >= 0.5:
        return "weak"
    return "scheduled"


def rank_questions(
    connection: sqlite3.Connection,
    now: datetime,
    filters: SessionFilters,
) -> list[RankedQuestion]:
    """Ordena o banco elegível por fórmula estável e sem aleatoriedade."""

    parameters: list[object] = []
    discipline_clause = ""
    if filters.disciplines:
        placeholders = ",".join("?" for _ in filters.disciplines)
        discipline_clause = f" AND question.discipline IN ({placeholders})"
        parameters.extend(filters.disciplines)
    rows = connection.execute(
        f"""
        SELECT question.id, question.topic_id, question.discipline,
               topic.historical_frequency, topic.covered,
               review.next_review_at, review.mastery, review.last_attempt_at,
               (
                   SELECT count(*)
                   FROM attempt_events AS attempt
                   JOIN questions AS attempted_question
                     ON attempted_question.id = attempt.question_id
                   WHERE attempted_question.topic_id = question.topic_id
               ) AS topic_attempts
        FROM questions AS question
        JOIN syllabus_topics AS topic ON topic.id = question.topic_id
        LEFT JOIN review_state AS review ON review.question_id = question.id
        WHERE question.status = 'validated' AND question.relevance = 'direct'
          AND question.delivery_policy = 'replayable'
        {discipline_clause}
        """,
        tuple(parameters),
    ).fetchall()

    instant = _aware(now)
    ranked: list[RankedQuestion] = []
    for row in rows:
        (
            question_id,
            topic_id,
            discipline,
            historical_frequency,
            covered,
            next_review_raw,
            mastery_raw,
            last_attempt_raw,
            topic_attempts,
        ) = row
        next_review = _parse_time(next_review_raw)
        last_attempt = _parse_time(last_attempt_raw)
        unseen = last_attempt is None
        overdue_days = (
            max((instant - next_review).total_seconds() / 86_400, 0.0)
            if next_review is not None
            else 0.0
        )
        overdue_score = _clamp(overdue_days / 30)
        weakness_score = 0.0 if mastery_raw is None else _clamp(1 - mastery_raw)
        historical_score = _clamp(float(historical_frequency))
        uncovered_score = float(not bool(covered))
        recency_score = (
            1.0
            if last_attempt is None
            else _clamp((instant - last_attempt).total_seconds() / 86_400 / 30)
        )
        weakness_weight = 0.40 if topic_attempts >= 20 else 0.30
        historical_weight = 0.05 if topic_attempts >= 20 else 0.15
        priority = (
            0.40 * overdue_score
            + weakness_weight * weakness_score
            + historical_weight * historical_score
            + 0.10 * uncovered_score
            + 0.05 * recency_score
        )
        ranked.append(
            RankedQuestion(
                question_id=str(question_id),
                topic_id=str(topic_id),
                discipline=str(discipline),
                priority=round(priority, 8),
                reason=_reason(
                    overdue_score, weakness_score, unseen, historical_score
                ),
            )
        )
    return sorted(
        ranked,
        key=lambda item: (-item.priority, item.topic_id, item.question_id),
    )


def calculate_next_review(
    attempts: Sequence[PriorAttempt],
    result: bool,
    confidence: int,
    now: datetime,
) -> ReviewDecision:
    """Calcula o próximo intervalo a partir do resultado e da sequência recente."""

    if confidence not in range(4):
        raise ValueError("A confiança deve estar entre 0 e 3.")
    instant = _aware(now)
    if not result or confidence == 0:
        interval = 1
        streak = 0
    elif confidence == 1:
        interval = 3
        streak = 0
    else:
        previous_streak = 0
        for attempt in reversed(attempts):
            if not attempt.correct or attempt.confidence < 2:
                break
            previous_streak += 1
        streak = previous_streak + 1
        base = 7 if confidence == 2 else 14
        interval = min(base * (2**previous_streak), 60)
    return ReviewDecision(
        next_review_at=instant + timedelta(days=interval),
        interval_days=interval,
        consecutive_confident_correct=streak,
    )
