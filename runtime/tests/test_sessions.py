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


def test_markdown_didatico_recebe_topico_na_importacao(db_connection):
    """Sem tópico o trecho nunca é elegível para geração; a importação já classifica."""

    import tempfile
    from pathlib import Path

    from pmal_study.markdown_import import import_markdown

    with tempfile.TemporaryDirectory() as temporary:
        _import_palmares(Path(temporary), import_markdown, db_connection)

    topics = [row[0] for row in db_connection.execute(
        "SELECT topic_id FROM source_chunks WHERE discipline = 'conhecimentos_alagoas'"
    )]
    assert topics and all(topic and topic.startswith("al.") for topic in topics)


def _import_palmares(tmp_path, import_markdown, db_connection):
    folder = tmp_path / "Conhecimentos AL"
    folder.mkdir()
    document = folder / "palmares.md"
    document.write_text(
        "# Quilombo dos Palmares\n\n## Liderança de Zumbi dos Palmares\n\n"
        "O Quilombo dos Palmares resistiu à escravidão sob a liderança de Zumbi dos Palmares.\n",
        encoding="utf-8",
    )
    import_markdown(document, tmp_path, db_connection)


def test_brief_de_geracao_nunca_tem_localizador_vazio(db_connection, frozen_now):
    """Trecho sem localizador recebe a página, como na busca de evidências."""

    from pmal_study.generation import GenerationService

    with db_connection:
        db_connection.execute(
            "INSERT INTO source_documents(id, relative_path, document_type, sha256, page_count, status) "
            "VALUES ('doc-x', 'Legislacao Oficial/L9099.pdf', 'pdf', ?, 3, 'usable')", ("e" * 64,),
        )
        db_connection.execute(
            "INSERT INTO source_document_versions(id, document_id, sha256, page_count, status, processed_at, is_current) "
            "VALUES ('ver-x', 'doc-x', ?, 3, 'usable', ?, 1)", ("e" * 64, frozen_now.isoformat()),
        )
        db_connection.execute(
            "INSERT INTO source_chunks(id, version_id, document_id, page_start, page_end, locator, text, sha256, "
            "discipline, topic_id, authority, status) VALUES ('chunk-x', 'ver-x', 'doc-x', 2, 2, NULL, "
            "'Art. 61. Consideram-se infrações penais de menor potencial ofensivo as contravenções penais.', ?, "
            "'legislacao_pmal', 'leg.15', 'local_official_copy', 'usable')", ("f" * 64,),
        )
        session = StudyService(db_connection, clock=lambda: frozen_now).start_session(
            mode="discipline", duration_minutes=30, disciplines=["legislacao_pmal"],
        )
        db_connection.execute(
            "INSERT INTO generation_jobs(id, session_id, discipline, topic_id, concept_key, difficulty, "
            "pedagogical_reason, update_policy, status, prepared_at) VALUES ('job-x', ?, 'legislacao_pmal', "
            "'leg.15', 'legacy:leg.15', 3, 'teste', 'local_sufficient', 'prepared', ?)",
            (session.id, frozen_now.isoformat()),
        )
        db_connection.execute(
            "INSERT INTO generation_evidence(job_id, evidence_kind, source_chunk_id, rank) VALUES ('job-x', 'local', 'chunk-x', 1)"
        )

    brief = GenerationService(db_connection, clock=lambda: frozen_now)._brief("job-x")
    assert brief.evidence[0].locator == "p. 2"
