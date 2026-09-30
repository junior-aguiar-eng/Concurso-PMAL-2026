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

    assert dashboard["study_bank"] == 23
    assert dashboard["attempts_total"] == 0
    assert len(dashboard["discipline_metrics"]) == 4
    assert dashboard["syllabus"]["topics_total"] == 109


def test_evidencia_ao_vivo_na_correcao_usa_url_do_snapshot(db_connection, frozen_now):
    """A URL da evidência ao vivo vem do snapshot web, não do documento local da questão."""

    service = StudyService(db_connection, clock=lambda: frozen_now)
    session = service.start_session(mode="timed", duration_minutes=30, disciplines=None)
    question = service.next_question(session.id)
    url = "https://www.al.gov.br/historia/penedo"
    with db_connection:
        db_connection.execute(
            """
            INSERT INTO live_source_snapshots(
                id, url, final_url, source_kind, locator, confirmed_excerpt, sha256,
                retrieved_at, authority, status
            ) VALUES ('snap-1', ?, ?, 'alagoas_governo', 'Penedo', 'trecho', ?, ?, 'official_web', 'verified')
            """,
            (url, url, "a" * 64, frozen_now.isoformat()),
        )
        db_connection.execute(
            """
            INSERT INTO question_evidence(
                question_id, claim_key, evidence_kind, live_snapshot_id, locator, excerpt,
                evidence_hash, authority
            ) VALUES (?, 'fundacao', 'live', 'snap-1', 'Penedo', 'trecho', ?, 'official_web')
            """,
            (question.id, "b" * 64),
        )

    result = service.submit_answer(
        session_id=session.id, question_id=question.id, answer="C", confidence=1,
        attempt_id="attempt-live-url",
    )

    live = [item for item in result.evidence if item.kind == "live"]
    assert [item.url for item in live] == [url]
    assert live[0].source == url
    assert live[0].retrieved_at == frozen_now.isoformat()
