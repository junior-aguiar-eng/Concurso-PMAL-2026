"""Testes unitários para o agendador de revisões espaçadas."""

from datetime import UTC, datetime, timedelta
import pytest

from pmal_study.scheduler import (
    PriorAttempt,
    SessionFilters,
    calculate_next_review,
    rank_questions,
)


def test_session_filters_valid_disciplines():
    filters = SessionFilters(disciplines=("direito_penal_militar",))
    assert filters.disciplines == ("direito_penal_militar",)


def test_session_filters_invalid_discipline():
    with pytest.raises(ValueError, match="Disciplinas fora do escopo"):
        SessionFilters(disciplines=("informatica",))


def test_calculate_next_review_incorrect():
    now = datetime(2026, 9, 22, 12, 0, 0, tzinfo=UTC)
    decision = calculate_next_review(
        attempts=[PriorAttempt(correct=True, confidence=3)],
        result=False,
        confidence=2,
        now=now,
    )
    assert decision.interval_days == 1
    assert decision.consecutive_confident_correct == 0
    assert decision.next_review_at == now + timedelta(days=1)


def test_calculate_next_review_low_confidence():
    now = datetime(2026, 9, 22, 12, 0, 0, tzinfo=UTC)
    # Acertou, mas com confiança 0 -> revisão em 1 dia
    decision = calculate_next_review(
        attempts=[],
        result=True,
        confidence=0,
        now=now,
    )
    assert decision.interval_days == 1
    assert decision.consecutive_confident_correct == 0

    # Confiança 1 -> revisão em 3 dias
    decision1 = calculate_next_review(
        attempts=[],
        result=True,
        confidence=1,
        now=now,
    )
    assert decision1.interval_days == 3
    assert decision1.consecutive_confident_correct == 0


def test_calculate_next_review_high_confidence_progression():
    now = datetime(2026, 9, 22, 12, 0, 0, tzinfo=UTC)
    # 1º acerto confiante (confiança 3) -> 14 dias
    d1 = calculate_next_review([], result=True, confidence=3, now=now)
    assert d1.interval_days == 14
    assert d1.consecutive_confident_correct == 1

    # 2º acerto confiante consecutivo -> 14 * 2^1 = 28 dias
    d2 = calculate_next_review(
        [PriorAttempt(correct=True, confidence=3)],
        result=True,
        confidence=3,
        now=now,
    )
    assert d2.interval_days == 28
    assert d2.consecutive_confident_correct == 2


def test_rank_questions_deterministic(db_connection, frozen_now):
    filters = SessionFilters()
    ranked1 = rank_questions(db_connection, frozen_now, filters)
    ranked2 = rank_questions(db_connection, frozen_now, filters)

    assert len(ranked1) > 0
    assert [q.question_id for q in ranked1] == [q.question_id for q in ranked2]
    assert all(q.priority >= 0 for q in ranked1)
