"""Schema SQLite versionado do Treinador PMAL Oficial."""

from __future__ import annotations

import sqlite3

from pmal_study.db import transaction

SCHEMA_VERSION = 11

MIGRATION_1 = (
    """
    CREATE TABLE source_documents (
        id TEXT PRIMARY KEY,
        relative_path TEXT NOT NULL UNIQUE,
        document_type TEXT NOT NULL,
        sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
        page_count INTEGER NOT NULL CHECK(page_count >= 0),
        status TEXT NOT NULL CHECK(status IN ('usable', 'ocr_required', 'extraction_failed', 'metadata_only')),
        quality REAL CHECK(quality IS NULL OR (quality >= 0 AND quality <= 1)),
        extraction_diagnostics TEXT,
        processed_at TEXT
    )
    """,
    """
    CREATE TABLE source_pages (
        document_id TEXT NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
        page_number INTEGER NOT NULL CHECK(page_number > 0),
        text TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL CHECK(status IN ('usable', 'ocr_required', 'extraction_failed', 'bundled_reference')),
        letter_ratio REAL CHECK(letter_ratio IS NULL OR (letter_ratio >= 0 AND letter_ratio <= 1)),
        replacement_ratio REAL CHECK(replacement_ratio IS NULL OR (replacement_ratio >= 0 AND replacement_ratio <= 1)),
        PRIMARY KEY (document_id, page_number)
    )
    """,
    """
    CREATE VIRTUAL TABLE source_pages_fts USING fts5(
        text,
        content='source_pages',
        content_rowid='rowid'
    )
    """,
    """
    CREATE TRIGGER source_pages_ai AFTER INSERT ON source_pages BEGIN
        INSERT INTO source_pages_fts(rowid, text) VALUES (new.rowid, new.text);
    END
    """,
    """
    CREATE TRIGGER source_pages_ad AFTER DELETE ON source_pages BEGIN
        INSERT INTO source_pages_fts(source_pages_fts, rowid, text)
        VALUES ('delete', old.rowid, old.text);
    END
    """,
    """
    CREATE TRIGGER source_pages_au AFTER UPDATE OF text ON source_pages BEGIN
        INSERT INTO source_pages_fts(source_pages_fts, rowid, text)
        VALUES ('delete', old.rowid, old.text);
        INSERT INTO source_pages_fts(rowid, text) VALUES (new.rowid, new.text);
    END
    """,
    """
    CREATE TABLE syllabus_topics (
        id TEXT PRIMARY KEY,
        discipline TEXT NOT NULL CHECK(discipline IN (
            'direito_penal_militar',
            'direito_processual_penal_militar',
            'legislacao_pmal',
            'conhecimentos_alagoas'
        )),
        parent_id TEXT REFERENCES syllabus_topics(id) ON DELETE RESTRICT,
        title TEXT NOT NULL,
        order_index INTEGER NOT NULL CHECK(order_index >= 0),
        depth INTEGER NOT NULL CHECK(depth >= 0),
        source_document TEXT NOT NULL,
        source_page INTEGER NOT NULL CHECK(source_page > 0),
        covered INTEGER NOT NULL DEFAULT 0 CHECK(covered IN (0, 1)),
        historical_frequency REAL NOT NULL DEFAULT 0 CHECK(historical_frequency >= 0),
        UNIQUE(discipline, parent_id, order_index)
    )
    """,
    """
    CREATE TABLE topic_sources (
        topic_id TEXT NOT NULL REFERENCES syllabus_topics(id) ON DELETE CASCADE,
        document_id TEXT NOT NULL,
        page_number INTEGER NOT NULL,
        PRIMARY KEY (topic_id, document_id, page_number),
        FOREIGN KEY (document_id, page_number)
            REFERENCES source_pages(document_id, page_number) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE questions (
        id TEXT PRIMARY KEY,
        origin TEXT NOT NULL CHECK(origin IN ('official', 'adapted', 'original')),
        year INTEGER,
        cargo TEXT,
        number INTEGER,
        discipline TEXT NOT NULL CHECK(discipline IN (
            'direito_penal_militar',
            'direito_processual_penal_militar',
            'legislacao_pmal',
            'conhecimentos_alagoas'
        )),
        topic_id TEXT REFERENCES syllabus_topics(id) ON DELETE RESTRICT,
        statement TEXT NOT NULL,
        answer TEXT CHECK(answer IN ('C', 'E')),
        rationale TEXT,
        status TEXT NOT NULL CHECK(status IN (
            'validated', 'needs_answer_key', 'needs_review', 'rejected'
        )),
        relevance TEXT NOT NULL CHECK(relevance IN ('direct', 'style_only', 'out_of_scope')),
        adaptation_note TEXT,
        validated_at TEXT
    )
    """,
    """
    CREATE TABLE question_sources (
        question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
        document_id TEXT NOT NULL,
        page_number INTEGER NOT NULL,
        PRIMARY KEY (question_id, document_id, page_number),
        FOREIGN KEY (document_id, page_number)
            REFERENCES source_pages(document_id, page_number) ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE study_sessions (
        id TEXT PRIMARY KEY,
        mode TEXT NOT NULL CHECK(mode IN ('diagnostic', 'timed', 'discipline', 'review', 'mixed_mock')),
        duration_minutes INTEGER CHECK(duration_minutes IS NULL OR duration_minutes > 0),
        disciplines_json TEXT,
        started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        ended_at TEXT,
        status TEXT NOT NULL CHECK(status IN ('open', 'completed', 'interrupted'))
    )
    """,
    """
    CREATE TABLE attempt_events (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES study_sessions(id) ON DELETE RESTRICT,
        question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE RESTRICT,
        answer TEXT NOT NULL CHECK(answer IN ('C', 'E')),
        expected_answer TEXT NOT NULL CHECK(expected_answer IN ('C', 'E')),
        correct INTEGER NOT NULL CHECK(correct IN (0, 1)),
        confidence INTEGER NOT NULL CHECK(confidence BETWEEN 0 AND 3),
        duration_ms INTEGER CHECK(duration_ms IS NULL OR duration_ms >= 0),
        error_pattern TEXT,
        created_at TEXT NOT NULL,
        payload_hash TEXT NOT NULL CHECK(length(payload_hash) = 64)
    )
    """,
    """
    CREATE TABLE review_state (
        question_id TEXT PRIMARY KEY REFERENCES questions(id) ON DELETE CASCADE,
        next_review_at TEXT NOT NULL,
        interval_days INTEGER NOT NULL CHECK(interval_days BETWEEN 1 AND 60),
        consecutive_correct INTEGER NOT NULL DEFAULT 0 CHECK(consecutive_correct >= 0),
        mastery REAL NOT NULL DEFAULT 0 CHECK(mastery BETWEEN 0 AND 1),
        last_attempt_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_source_documents_status ON source_documents(status)",
    "CREATE INDEX idx_source_pages_status ON source_pages(status)",
    "CREATE INDEX idx_syllabus_discipline_order ON syllabus_topics(discipline, order_index)",
    "CREATE INDEX idx_questions_study_bank ON questions(status, relevance, discipline, topic_id)",
    "CREATE INDEX idx_attempts_question_created ON attempt_events(question_id, created_at)",
    "CREATE INDEX idx_attempts_session_created ON attempt_events(session_id, created_at)",
    "CREATE INDEX idx_review_due ON review_state(next_review_at)",
)

MIGRATION_2 = (
    "ALTER TABLE source_documents ADD COLUMN source_url TEXT",
    "ALTER TABLE source_documents ADD COLUMN retrieved_at TEXT",
    "ALTER TABLE questions ADD COLUMN answer_key_source TEXT",
    "ALTER TABLE questions ADD COLUMN answer_key_checked_at TEXT",
    """
    ALTER TABLE question_sources ADD COLUMN source_role TEXT NOT NULL
    DEFAULT 'question' CHECK(source_role IN ('question', 'rationale'))
    """,
    """
    CREATE TABLE source_checks (
        id TEXT PRIMARY KEY,
        url TEXT NOT NULL,
        source_kind TEXT NOT NULL CHECK(source_kind IN ('planalto', 'stf', 'stj', 'cebraspe')),
        retrieved_at TEXT NOT NULL,
        sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
        status TEXT NOT NULL CHECK(status IN ('verified', 'changed', 'failed')),
        citation TEXT NOT NULL,
        UNIQUE(url, retrieved_at)
    )
    """,
    "CREATE INDEX idx_source_checks_url_date ON source_checks(url, retrieved_at)",
)

MIGRATION_3 = (
    """
    CREATE TABLE source_document_versions (
        id TEXT PRIMARY KEY,
        document_id TEXT NOT NULL REFERENCES source_documents(id) ON DELETE RESTRICT,
        sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
        page_count INTEGER NOT NULL CHECK(page_count >= 0),
        status TEXT NOT NULL CHECK(status IN (
            'usable', 'ocr_required', 'extraction_failed', 'metadata_only', 'quarantined'
        )),
        processed_at TEXT,
        is_current INTEGER NOT NULL DEFAULT 1 CHECK(is_current IN (0, 1)),
        extraction_diagnostics TEXT,
        UNIQUE(document_id, sha256)
    )
    """,
    """
    INSERT INTO source_document_versions(
        id, document_id, sha256, page_count, status, processed_at,
        extraction_diagnostics, is_current
    )
    SELECT id || ':' || substr(sha256, 1, 16), id, sha256, page_count, status,
           processed_at, extraction_diagnostics, 1
    FROM source_documents
    """,
    """
    CREATE TABLE source_chunks (
        id TEXT PRIMARY KEY,
        version_id TEXT NOT NULL REFERENCES source_document_versions(id) ON DELETE RESTRICT,
        document_id TEXT NOT NULL REFERENCES source_documents(id) ON DELETE RESTRICT,
        page_start INTEGER NOT NULL CHECK(page_start > 0),
        page_end INTEGER NOT NULL CHECK(page_end >= page_start),
        locator TEXT,
        text TEXT NOT NULL,
        sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
        discipline TEXT CHECK(discipline IS NULL OR discipline IN (
            'direito_penal_militar',
            'direito_processual_penal_militar',
            'legislacao_pmal',
            'conhecimentos_alagoas'
        )),
        topic_id TEXT REFERENCES syllabus_topics(id) ON DELETE SET NULL,
        source_kind TEXT NOT NULL DEFAULT 'local',
        authority TEXT NOT NULL DEFAULT 'didactic',
        status TEXT NOT NULL DEFAULT 'usable' CHECK(status IN (
            'usable', 'empty', 'ocr', 'quarantined', 'superseded'
        )),
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(version_id, page_start, page_end, locator, sha256)
    )
    """,
    """
    CREATE VIRTUAL TABLE source_chunks_fts USING fts5(
        text, locator,
        content='source_chunks',
        content_rowid='rowid'
    )
    """,
    """
    CREATE TRIGGER source_chunks_ai AFTER INSERT ON source_chunks BEGIN
        INSERT INTO source_chunks_fts(rowid, text, locator)
        VALUES (new.rowid, new.text, coalesce(new.locator, ''));
    END
    """,
    """
    CREATE TRIGGER source_chunks_ad AFTER DELETE ON source_chunks BEGIN
        INSERT INTO source_chunks_fts(source_chunks_fts, rowid, text, locator)
        VALUES ('delete', old.rowid, old.text, coalesce(old.locator, ''));
    END
    """,
    """
    CREATE TRIGGER source_chunks_au AFTER UPDATE OF text, locator ON source_chunks BEGIN
        INSERT INTO source_chunks_fts(source_chunks_fts, rowid, text, locator)
        VALUES ('delete', old.rowid, old.text, coalesce(old.locator, ''));
        INSERT INTO source_chunks_fts(rowid, text, locator)
        VALUES (new.rowid, new.text, coalesce(new.locator, ''));
    END
    """,
    """
    CREATE TABLE live_source_snapshots (
        id TEXT PRIMARY KEY,
        url TEXT NOT NULL,
        final_url TEXT NOT NULL,
        source_kind TEXT NOT NULL,
        author TEXT,
        locator TEXT NOT NULL,
        confirmed_excerpt TEXT NOT NULL,
        sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
        retrieved_at TEXT NOT NULL,
        authority TEXT NOT NULL,
        previous_snapshot_id TEXT REFERENCES live_source_snapshots(id) ON DELETE SET NULL,
        status TEXT NOT NULL CHECK(status IN ('verified', 'changed', 'invalidated', 'failed')),
        UNIQUE(final_url, locator, sha256)
    )
    """,
    """
    CREATE TABLE exam_items (
        id TEXT PRIMARY KEY,
        source_question_id TEXT UNIQUE REFERENCES questions(id) ON DELETE SET NULL,
        statement TEXT NOT NULL,
        answer TEXT CHECK(answer IN ('C', 'E')),
        rationale TEXT,
        discipline_label TEXT,
        exam_name TEXT,
        cargo TEXT,
        board TEXT NOT NULL DEFAULT 'Cebraspe',
        year INTEGER,
        number INTEGER,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        review_status TEXT NOT NULL CHECK(review_status IN (
            'verified', 'needs_review', 'style_only', 'rejected'
        )),
        applicability TEXT NOT NULL CHECK(applicability IN (
            'applicable', 'style_only', 'out_of_scope'
        )),
        content_hash TEXT NOT NULL CHECK(length(content_hash) = 64),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    INSERT INTO exam_items(
        id, source_question_id, statement, answer, rationale, discipline_label,
        cargo, year, number, review_status, applicability, content_hash
    )
    SELECT 'legacy:' || id, id, statement, answer, rationale, discipline,
           cargo, year, number,
           CASE WHEN status = 'validated' THEN 'verified'
                WHEN status = 'rejected' THEN 'rejected'
                ELSE 'needs_review' END,
           CASE WHEN relevance = 'direct' THEN 'applicable'
                WHEN relevance = 'style_only' THEN 'style_only'
                ELSE 'out_of_scope' END,
           pmal_sha256(statement)
    FROM questions
    """,
    "ALTER TABLE questions ADD COLUMN generation_job_id TEXT",
    """
    ALTER TABLE questions ADD COLUMN delivery_policy TEXT NOT NULL DEFAULT 'replayable'
    CHECK(delivery_policy IN ('replayable', 'session_only'))
    """,
    "ALTER TABLE questions ADD COLUMN difficulty INTEGER CHECK(difficulty IS NULL OR difficulty BETWEEN 1 AND 5)",
    "ALTER TABLE questions ADD COLUMN concept_key TEXT",
    "ALTER TABLE questions ADD COLUMN content_hash TEXT",
    "UPDATE questions SET delivery_policy = 'session_only' WHERE origin = 'original'",
    "UPDATE questions SET concept_key = 'legacy:' || coalesce(topic_id, id)",
    "UPDATE questions SET content_hash = pmal_sha256(statement)",
    """
    CREATE TABLE generation_jobs (
        id TEXT PRIMARY KEY,
        session_id TEXT REFERENCES study_sessions(id) ON DELETE RESTRICT,
        discipline TEXT NOT NULL CHECK(discipline IN (
            'direito_penal_militar',
            'direito_processual_penal_militar',
            'legislacao_pmal',
            'conhecimentos_alagoas'
        )),
        topic_id TEXT REFERENCES syllabus_topics(id) ON DELETE RESTRICT,
        concept_key TEXT NOT NULL,
        difficulty INTEGER NOT NULL CHECK(difficulty BETWEEN 1 AND 5),
        pedagogical_reason TEXT NOT NULL,
        update_policy TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN (
            'prepared', 'committed', 'rejected', 'cancelled'
        )),
        prepared_at TEXT NOT NULL,
        committed_at TEXT,
        rejection_reason TEXT,
        question_id TEXT UNIQUE REFERENCES questions(id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE generation_evidence (
        job_id TEXT NOT NULL REFERENCES generation_jobs(id) ON DELETE CASCADE,
        evidence_kind TEXT NOT NULL CHECK(evidence_kind IN ('local', 'live')),
        source_chunk_id TEXT REFERENCES source_chunks(id) ON DELETE RESTRICT,
        live_snapshot_id TEXT REFERENCES live_source_snapshots(id) ON DELETE RESTRICT,
        rank INTEGER NOT NULL CHECK(rank > 0),
        role TEXT NOT NULL DEFAULT 'foundation',
        PRIMARY KEY(job_id, evidence_kind, rank),
        CHECK(
            (evidence_kind = 'local' AND source_chunk_id IS NOT NULL AND live_snapshot_id IS NULL)
            OR (evidence_kind = 'live' AND source_chunk_id IS NULL AND live_snapshot_id IS NOT NULL)
        )
    )
    """,
    """
    CREATE TABLE question_evidence (
        question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
        claim_key TEXT NOT NULL,
        evidence_kind TEXT NOT NULL CHECK(evidence_kind IN ('local', 'live')),
        source_chunk_id TEXT REFERENCES source_chunks(id) ON DELETE RESTRICT,
        live_snapshot_id TEXT REFERENCES live_source_snapshots(id) ON DELETE RESTRICT,
        locator TEXT NOT NULL,
        excerpt TEXT NOT NULL,
        evidence_hash TEXT NOT NULL CHECK(length(evidence_hash) = 64),
        authority TEXT NOT NULL,
        PRIMARY KEY(question_id, claim_key, evidence_hash),
        CHECK(
            (evidence_kind = 'local' AND source_chunk_id IS NOT NULL AND live_snapshot_id IS NULL)
            OR (evidence_kind = 'live' AND source_chunk_id IS NULL AND live_snapshot_id IS NOT NULL)
        )
    )
    """,
    """
    CREATE TABLE approved_exemplars (
        question_id TEXT PRIMARY KEY REFERENCES questions(id) ON DELETE RESTRICT,
        state TEXT NOT NULL CHECK(state IN ('approved', 'revoked')),
        approved_at TEXT NOT NULL,
        revoked_at TEXT,
        event_actor TEXT NOT NULL DEFAULT 'user',
        reason TEXT
    )
    """,
    """
    CREATE TABLE learning_targets (
        id TEXT PRIMARY KEY,
        discipline TEXT NOT NULL CHECK(discipline IN (
            'direito_penal_militar',
            'direito_processual_penal_militar',
            'legislacao_pmal',
            'conhecimentos_alagoas'
        )),
        topic_id TEXT REFERENCES syllabus_topics(id) ON DELETE RESTRICT,
        concept_key TEXT NOT NULL,
        title TEXT NOT NULL,
        historical_frequency REAL NOT NULL DEFAULT 0 CHECK(historical_frequency >= 0),
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
        UNIQUE(discipline, concept_key)
    )
    """,
    """
    INSERT INTO learning_targets(
        id, discipline, topic_id, concept_key, title, historical_frequency
    )
    SELECT 'legacy:' || id, discipline, id, 'legacy:' || id, title, historical_frequency
    FROM syllabus_topics
    """,
    """
    CREATE TABLE concept_review_state (
        learning_target_id TEXT PRIMARY KEY REFERENCES learning_targets(id) ON DELETE CASCADE,
        next_review_at TEXT NOT NULL,
        interval_days INTEGER NOT NULL CHECK(interval_days BETWEEN 1 AND 60),
        consecutive_correct INTEGER NOT NULL DEFAULT 0 CHECK(consecutive_correct >= 0),
        mastery REAL NOT NULL DEFAULT 0 CHECK(mastery BETWEEN 0 AND 1),
        last_attempt_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_question_id TEXT REFERENCES questions(id) ON DELETE SET NULL
    )
    """,
    """
    INSERT INTO concept_review_state(
        learning_target_id, next_review_at, interval_days, consecutive_correct,
        mastery, last_attempt_at, updated_at, last_question_id
    )
    SELECT 'legacy:' || q.topic_id, r.next_review_at, r.interval_days,
           r.consecutive_correct, r.mastery, r.last_attempt_at, r.updated_at, r.question_id
    FROM review_state r
    JOIN questions q ON q.id = r.question_id
    WHERE q.topic_id IS NOT NULL
      AND r.rowid = (
          SELECT r2.rowid
          FROM review_state r2
          JOIN questions q2 ON q2.id = r2.question_id
          WHERE q2.topic_id = q.topic_id
          ORDER BY r2.updated_at DESC, r2.rowid DESC
          LIMIT 1
      )
    """,
    "ALTER TABLE attempt_events ADD COLUMN learning_target_id TEXT REFERENCES learning_targets(id) ON DELETE RESTRICT",
    """
    UPDATE attempt_events
    SET learning_target_id = (
        SELECT 'legacy:' || q.topic_id FROM questions q WHERE q.id = attempt_events.question_id
    )
    WHERE EXISTS (
        SELECT 1 FROM questions q WHERE q.id = attempt_events.question_id AND q.topic_id IS NOT NULL
    )
    """,
    "CREATE INDEX idx_source_versions_current ON source_document_versions(document_id, is_current)",
    "CREATE INDEX idx_source_chunks_topic ON source_chunks(discipline, topic_id, status)",
    "CREATE INDEX idx_live_snapshots_url_date ON live_source_snapshots(final_url, retrieved_at)",
    "CREATE INDEX idx_exam_items_profile ON exam_items(board, year, cargo, applicability)",
    "CREATE INDEX idx_generation_jobs_session_status ON generation_jobs(session_id, status)",
    "CREATE INDEX idx_question_evidence_question ON question_evidence(question_id)",
    "CREATE INDEX idx_learning_targets_discipline ON learning_targets(discipline, active)",
    "CREATE INDEX idx_concept_review_due ON concept_review_state(next_review_at)",
)

MIGRATION_4 = (
    """
    ALTER TABLE source_pages ADD COLUMN page_state TEXT NOT NULL DEFAULT 'usable'
    CHECK(page_state IN ('usable', 'empty', 'ocr', 'quarantined', 'extraction_failed', 'bundled_reference'))
    """,
    """
    ALTER TABLE source_pages ADD COLUMN extraction_method TEXT NOT NULL DEFAULT 'text'
    CHECK(extraction_method IN ('text', 'ocr', 'bundled'))
    """,
    "ALTER TABLE source_pages ADD COLUMN content_hash TEXT",
    "ALTER TABLE source_pages ADD COLUMN quarantine_reason TEXT",
    """
    UPDATE source_pages
    SET page_state = CASE
        WHEN status = 'usable' THEN 'usable'
        WHEN status = 'ocr_required' THEN 'quarantined'
        WHEN status = 'extraction_failed' THEN 'extraction_failed'
        ELSE 'bundled_reference'
    END,
    extraction_method = CASE WHEN status = 'bundled_reference' THEN 'bundled' ELSE 'text' END,
    content_hash = pmal_sha256(text),
    quarantine_reason = CASE WHEN status = 'ocr_required' THEN 'OCR pendente na migração' END
    """,
    "CREATE INDEX idx_source_pages_state ON source_pages(page_state, extraction_method)",
)

MIGRATION_5 = (
    """
    CREATE TABLE exam_item_sources (
        exam_item_id TEXT NOT NULL REFERENCES exam_items(id) ON DELETE CASCADE,
        evidence_kind TEXT NOT NULL CHECK(evidence_kind IN ('local', 'live')),
        source_chunk_id TEXT REFERENCES source_chunks(id) ON DELETE RESTRICT,
        live_snapshot_id TEXT REFERENCES live_source_snapshots(id) ON DELETE RESTRICT,
        locator TEXT NOT NULL,
        PRIMARY KEY(exam_item_id, evidence_kind, locator),
        CHECK(
            (evidence_kind = 'local' AND source_chunk_id IS NOT NULL AND live_snapshot_id IS NULL)
            OR (evidence_kind = 'live' AND source_chunk_id IS NULL AND live_snapshot_id IS NOT NULL)
        )
    )
    """,
    "CREATE INDEX idx_exam_item_sources_item ON exam_item_sources(exam_item_id)",
)

MIGRATION_6 = (
    "ALTER TABLE source_checks RENAME TO source_checks_v5",
    """
    CREATE TABLE source_checks (
        id TEXT PRIMARY KEY,
        url TEXT NOT NULL,
        source_kind TEXT NOT NULL CHECK(source_kind IN (
            'planalto', 'stf', 'stj', 'cebraspe', 'pmal', 'alagoas_governo',
            'aleal', 'tjal', 'ibge', 'ufal', 'academic'
        )),
        retrieved_at TEXT NOT NULL,
        sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
        status TEXT NOT NULL CHECK(status IN ('verified', 'changed', 'failed')),
        citation TEXT NOT NULL,
        UNIQUE(url, retrieved_at)
    )
    """,
    """
    INSERT INTO source_checks(id, url, source_kind, retrieved_at, sha256, status, citation)
    SELECT id, url, source_kind, retrieved_at, sha256, status, citation
    FROM source_checks_v5
    """,
    "DROP TABLE source_checks_v5",
    "CREATE INDEX idx_source_checks_url_date ON source_checks(url, retrieved_at)",
)

MIGRATION_7 = (
    "ALTER TABLE questions ADD COLUMN construction_pattern TEXT",
    "ALTER TABLE questions ADD COLUMN decisive_expression TEXT",
    "ALTER TABLE questions ADD COLUMN trap TEXT",
    "ALTER TABLE questions ADD COLUMN distinction TEXT",
    """
    CREATE TABLE session_questions (
        session_id TEXT NOT NULL REFERENCES study_sessions(id) ON DELETE RESTRICT,
        question_id TEXT NOT NULL UNIQUE REFERENCES questions(id) ON DELETE RESTRICT,
        generation_job_id TEXT REFERENCES generation_jobs(id) ON DELETE RESTRICT,
        assigned_at TEXT NOT NULL,
        PRIMARY KEY(session_id, question_id)
    )
    """,
    "CREATE INDEX idx_session_questions_session ON session_questions(session_id, assigned_at)",
)

MIGRATION_8 = (
    "ALTER TABLE session_questions RENAME TO session_questions_v7",
    """
    CREATE TABLE session_questions (
        session_id TEXT NOT NULL REFERENCES study_sessions(id) ON DELETE RESTRICT,
        question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE RESTRICT,
        generation_job_id TEXT REFERENCES generation_jobs(id) ON DELETE RESTRICT,
        assigned_at TEXT NOT NULL,
        PRIMARY KEY(session_id, question_id)
    )
    """,
    """
    INSERT INTO session_questions(session_id, question_id, generation_job_id, assigned_at)
    SELECT session_id, question_id, generation_job_id, assigned_at
    FROM session_questions_v7
    """,
    "DROP TABLE session_questions_v7",
    "CREATE INDEX idx_session_questions_session ON session_questions(session_id, assigned_at)",
)

MIGRATION_9 = (
    """
    CREATE TABLE exemplar_events (
        id TEXT PRIMARY KEY,
        question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE RESTRICT,
        action TEXT NOT NULL CHECK(action IN ('approve', 'revoke')),
        actor TEXT NOT NULL,
        reason TEXT,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_exemplar_events_question_date ON exemplar_events(question_id, created_at)",
)

MIGRATION_10 = (
    """
    CREATE TABLE dissection_exchanges (
        id TEXT PRIMARY KEY,
        attempt_id TEXT NOT NULL REFERENCES attempt_events(id) ON DELETE RESTRICT,
        sequence INTEGER NOT NULL CHECK(sequence > 0),
        user_message TEXT NOT NULL CHECK(length(trim(user_message)) > 0),
        assistant_message TEXT NOT NULL CHECK(length(trim(assistant_message)) > 0),
        model TEXT NOT NULL,
        created_at TEXT NOT NULL,
        payload_hash TEXT NOT NULL CHECK(length(payload_hash) = 64),
        UNIQUE(attempt_id, sequence)
    )
    """,
    "CREATE INDEX idx_dissection_attempt_sequence ON dissection_exchanges(attempt_id, sequence)",
    """
    CREATE TABLE operation_receipts (
        id TEXT PRIMARY KEY,
        operation_kind TEXT NOT NULL,
        request_hash TEXT NOT NULL CHECK(length(request_hash) = 64),
        response_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_operation_receipts_kind_date ON operation_receipts(operation_kind, created_at)",
)

MIGRATION_11 = (
    """
    CREATE TABLE host_jobs (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL CHECK(kind IN ('question_generation', 'dissection')),
        generation_job_id TEXT UNIQUE REFERENCES generation_jobs(id) ON DELETE RESTRICT,
        attempt_id TEXT REFERENCES attempt_events(id) ON DELETE RESTRICT,
        user_message TEXT,
        status TEXT NOT NULL CHECK(status IN ('pending', 'processing', 'completed', 'failed')),
        result_json TEXT,
        error_code TEXT,
        error_message TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        payload_hash TEXT NOT NULL CHECK(length(payload_hash) = 64),
        CHECK(
            (kind = 'question_generation' AND generation_job_id IS NOT NULL
             AND attempt_id IS NULL AND user_message IS NULL)
            OR
            (kind = 'dissection' AND generation_job_id IS NULL
             AND attempt_id IS NOT NULL AND length(trim(user_message)) > 0)
        )
    )
    """,
    "CREATE INDEX idx_host_jobs_status_date ON host_jobs(status, created_at)",
    "CREATE INDEX idx_host_jobs_attempt ON host_jobs(attempt_id, created_at)",
)

MIGRATIONS = {
    1: MIGRATION_1,
    2: MIGRATION_2,
    3: MIGRATION_3,
    4: MIGRATION_4,
    5: MIGRATION_5,
    6: MIGRATION_6,
    7: MIGRATION_7,
    8: MIGRATION_8,
    9: MIGRATION_9,
    10: MIGRATION_10,
    11: MIGRATION_11,
}


def run_migrations(connection: sqlite3.Connection) -> None:
    """Atualiza uma conexão até a versão de schema suportada."""

    current_version = connection.execute("PRAGMA user_version").fetchone()[0]
    if current_version > SCHEMA_VERSION:
        raise RuntimeError(
            f"Banco na versão {current_version}; aplicação suporta {SCHEMA_VERSION}."
        )
    for target_version in range(current_version + 1, SCHEMA_VERSION + 1):
        with transaction(connection):
            for statement in MIGRATIONS[target_version]:
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {target_version}")
