"""Schema SQLite versionado do Treinador PMAL Oficial."""

from __future__ import annotations

import sqlite3

from pmal_study.db import transaction

SCHEMA_VERSION = 2

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

MIGRATIONS = {
    1: MIGRATION_1,
    2: MIGRATION_2,
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
