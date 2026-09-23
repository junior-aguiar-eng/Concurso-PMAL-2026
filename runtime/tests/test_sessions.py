"""Testes unitários e de integração para o StudyService."""

from datetime import UTC, datetime, timedelta
import pytest

from pmal_study.sessions import DomainError, StudyService


def test_start_session_and_next_question(db_connection, frozen_now):
    service = StudyService(db_connection, clock=lambda: frozen_now)

    session = service.start_session(mode="timed", duration_minutes=30, disciplines=None)
    assert session.status == "open"
    assert session.mode == "timed"

    question = service.next_question(session.id)
    assert question.id
    assert question.statement
    assert question.discipline in (
        "direito_penal_militar",
        "direito_processual_penal_militar",
        "legislacao_pmal",
        "conhecimentos_alagoas",
    )
    # Zero-knowledge check: PublicQuestion não deve ter gabarito exposto
    assert not hasattr(question, "answer")
    assert not hasattr(question, "expected_answer")
    assert not hasattr(question, "rationale")


def test_submit_answer_and_idempotency(db_connection, frozen_now):
    service = StudyService(db_connection, clock=lambda: frozen_now)
    session = service.start_session(mode="timed", duration_minutes=30, disciplines=None)
    question = service.next_question(session.id)

    attempt_id = "attempt-test-123"
    result1 = service.submit_answer(
        session_id=session.id,
        question_id=question.id,
        answer="C",
        confidence=2,
        attempt_id=attempt_id,
    )
    assert result1.expected_answer in ("C", "E")
    assert result1.rationale
    assert result1.source_label

    # Chamada idempotente com o mesmo attempt_id deve retornar o mesmo resultado
    result2 = service.submit_answer(
        session_id=session.id,
        question_id=question.id,
        answer="C",
        confidence=2,
        attempt_id=attempt_id,
    )
    assert result1.correct == result2.correct
    assert result1.expected_answer == result2.expected_answer
    assert result1.next_review_at == result2.next_review_at


def test_session_expiration(db_connection, frozen_now):
    current_time = frozen_now
    service = StudyService(db_connection, clock=lambda: current_time)
    session = service.start_session(mode="timed", duration_minutes=10, disciplines=None)

    # Avança o relógio em 11 minutos
    current_time = frozen_now + timedelta(minutes=11)

    with pytest.raises(DomainError) as exc:
        service.next_question(session.id)
    assert exc.value.code == "session_expired"


def test_dashboard_metrics(db_connection, frozen_now):
    service = StudyService(db_connection, clock=lambda: frozen_now)
    dashboard = service.get_dashboard()

    assert dashboard["study_bank"] == 19
    assert dashboard["attempts_total"] == 0
    assert len(dashboard["discipline_metrics"]) == 4
    assert dashboard["syllabus"]["topics_total"] == 109
