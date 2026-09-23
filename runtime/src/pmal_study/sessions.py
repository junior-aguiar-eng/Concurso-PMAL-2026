"""Serviço autoritativo de sessões, correção e progresso persistente."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable

from pmal_study.db import transaction
from pmal_study.models import Discipline, GradingResult, PublicQuestion
from pmal_study.scheduler import (
    PriorAttempt,
    SessionFilters,
    calculate_next_review,
    rank_questions,
)


_MODES = {"diagnostic", "timed", "discipline", "review", "mixed_mock"}
_ORIGIN_LABELS = {
    "official": "oficial",
    "adapted": "adaptada",
    "original": "inédita",
}


class DomainError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SessionView:
    id: str
    mode: str
    duration_minutes: int | None
    disciplines: tuple[str, ...]
    started_at: datetime
    status: str


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


class StudyService:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.connection = connection
        self._clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        value = self._clock()
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def _eligible_ids(
        self, mode: str, disciplines: tuple[str, ...]
    ) -> list[str]:
        now = self._now()
        ranked = rank_questions(
            self.connection,
            now,
            SessionFilters(disciplines=disciplines),
        )
        identifiers = [item.question_id for item in ranked]
        if mode == "diagnostic":
            attempted = {
                row[0]
                for row in self.connection.execute(
                    "SELECT DISTINCT question_id FROM attempt_events"
                ).fetchall()
            }
            identifiers = [item for item in identifiers if item not in attempted]
        elif mode == "review":
            due = {
                row[0]
                for row in self.connection.execute(
                    "SELECT question_id FROM review_state WHERE next_review_at <= ?",
                    (now.isoformat(),),
                ).fetchall()
            }
            identifiers = [item for item in identifiers if item in due]
        return identifiers

    def start_session(
        self,
        mode: str,
        duration_minutes: int | None,
        disciplines: list[str] | tuple[str, ...] | None,
    ) -> SessionView:
        if mode not in _MODES:
            raise DomainError("invalid_session_mode", "Modo de sessão inválido.")
        selected = tuple(disciplines or ())
        try:
            SessionFilters(disciplines=selected)
        except ValueError as error:
            raise DomainError(
                "discipline_out_of_scope",
                "Disciplina fora do recorte PMAL Oficial.",
            ) from error
        if mode == "discipline" and not selected:
            raise DomainError(
                "discipline_required", "O modo por disciplina exige uma disciplina."
            )
        if duration_minutes is not None and duration_minutes <= 0:
            raise DomainError("invalid_duration", "A duração deve ser positiva.")
        if not self._eligible_ids(mode, selected):
            raise DomainError(
                "no_eligible_questions", "Não há questão validada elegível para a sessão."
            )

        session_id = str(uuid.uuid4())
        started_at = self._now()
        self.connection.execute(
            """
            INSERT INTO study_sessions(
                id, mode, duration_minutes, disciplines_json, started_at, status
            ) VALUES (?, ?, ?, ?, ?, 'open')
            """,
            (
                session_id,
                mode,
                duration_minutes,
                json.dumps(selected, ensure_ascii=False, separators=(",", ":")),
                started_at.isoformat(),
            ),
        )
        return SessionView(
            id=session_id,
            mode=mode,
            duration_minutes=duration_minutes,
            disciplines=selected,
            started_at=started_at,
            status="open",
        )

    def _session(self, session_id: str) -> tuple[str, int | None, tuple[str, ...], datetime, str]:
        row = self.connection.execute(
            """
            SELECT mode, duration_minutes, disciplines_json, started_at, status
            FROM study_sessions WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            raise DomainError("session_not_found", "Sessão não localizada.")
        mode, duration, raw_disciplines, started_at, status = row
        return (
            str(mode),
            duration,
            tuple(json.loads(raw_disciplines or "[]")),
            _parse_time(started_at),
            str(status),
        )

    def _require_open_session(
        self, session_id: str
    ) -> tuple[str, int | None, tuple[str, ...], datetime]:
        mode, duration, disciplines, started_at, status = self._session(session_id)
        if status != "open":
            raise DomainError("session_closed", "A sessão não está aberta.")
        if duration is not None and self._now() >= started_at + timedelta(minutes=duration):
            self.connection.execute(
                "UPDATE study_sessions SET status = 'completed', ended_at = ? WHERE id = ?",
                (self._now().isoformat(), session_id),
            )
            raise DomainError("session_expired", "O tempo da sessão foi encerrado.")
        return mode, duration, disciplines, started_at

    def next_question(self, session_id: str) -> PublicQuestion:
        mode, _, disciplines, _ = self._require_open_session(session_id)
        attempted = {
            row[0]
            for row in self.connection.execute(
                "SELECT question_id FROM attempt_events WHERE session_id = ?",
                (session_id,),
            ).fetchall()
        }
        available = [
            identifier
            for identifier in self._eligible_ids(mode, disciplines)
            if identifier not in attempted
        ]
        if not available:
            self.connection.execute(
                "UPDATE study_sessions SET status = 'completed', ended_at = ? WHERE id = ?",
                (self._now().isoformat(), session_id),
            )
            raise DomainError("session_complete", "Não há mais questões nesta sessão.")
        row = self.connection.execute(
            """
            SELECT id, discipline, topic_id, statement, origin
            FROM questions WHERE id = ?
            """,
            (available[0],),
        ).fetchone()
        return PublicQuestion(
            id=row[0],
            discipline=Discipline(row[1]),
            topic_id=row[2],
            statement=row[3],
            origin_label=_ORIGIN_LABELS[row[4]],
        )

    def _grading_result(
        self,
        question_id: str,
        *,
        correct: bool,
        expected_answer: str,
        error_pattern: str | None,
    ) -> GradingResult:
        question = self.connection.execute(
            "SELECT rationale FROM questions WHERE id = ?", (question_id,)
        ).fetchone()
        source = self.connection.execute(
            """
            SELECT document.relative_path, link.page_number
            FROM question_sources AS link
            JOIN source_documents AS document ON document.id = link.document_id
            WHERE link.question_id = ?
            ORDER BY CASE link.source_role WHEN 'rationale' THEN 0 ELSE 1 END,
                     document.relative_path, link.page_number
            LIMIT 1
            """,
            (question_id,),
        ).fetchone()
        review = self.connection.execute(
            "SELECT next_review_at FROM review_state WHERE question_id = ?",
            (question_id,),
        ).fetchone()
        return GradingResult(
            correct=correct,
            expected_answer=expected_answer,
            rationale=question[0],
            source_label=source[0] if source else "fonte não vinculada",
            source_page=source[1] if source else 0,
            next_review_at=_parse_time(review[0]),
            error_pattern=error_pattern,
        )

    def submit_answer(
        self,
        session_id: str,
        question_id: str,
        answer: str,
        confidence: int,
        *,
        attempt_id: str | None = None,
        error_pattern: str | None = None,
    ) -> GradingResult:
        if answer not in {"C", "E"}:
            raise DomainError("invalid_answer", "A resposta deve ser C ou E.")
        if confidence not in range(4):
            raise DomainError("invalid_confidence", "A confiança deve estar entre 0 e 3.")
        identifier = attempt_id or str(uuid.uuid4())
        payload = {
            "session_id": session_id,
            "question_id": question_id,
            "answer": answer,
            "confidence": confidence,
            "error_pattern": error_pattern,
        }
        payload_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        previous = self.connection.execute(
            """
            SELECT payload_hash, correct, expected_answer, error_pattern
            FROM attempt_events WHERE id = ?
            """,
            (identifier,),
        ).fetchone()
        if previous is not None:
            if previous[0] != payload_hash:
                raise DomainError(
                    "attempt_conflict", "Conflito de idempotência na tentativa."
                )
            return self._grading_result(
                question_id,
                correct=bool(previous[1]),
                expected_answer=previous[2],
                error_pattern=previous[3],
            )

        mode, _, disciplines, _ = self._require_open_session(session_id)
        if question_id not in self._eligible_ids(mode, disciplines):
            raise DomainError("question_not_eligible", "Questão não elegível para a sessão.")
        if self.connection.execute(
            "SELECT 1 FROM attempt_events WHERE session_id = ? AND question_id = ?",
            (session_id, question_id),
        ).fetchone():
            raise DomainError("question_already_answered", "Questão já respondida na sessão.")
        question = self.connection.execute(
            "SELECT answer FROM questions WHERE id = ?", (question_id,)
        ).fetchone()
        if question is None:
            raise DomainError("question_not_found", "Questão não localizada.")
        expected_answer = question[0]
        correct = answer == expected_answer
        pattern = error_pattern or (None if correct else "incorrect_judgment")
        prior_attempts = [
            PriorAttempt(correct=bool(row[0]), confidence=row[1])
            for row in self.connection.execute(
                """
                SELECT correct, confidence FROM attempt_events
                WHERE question_id = ? ORDER BY created_at, id
                """,
                (question_id,),
            ).fetchall()
        ]
        decision = calculate_next_review(
            prior_attempts, correct, confidence, self._now()
        )
        previous_mastery = self.connection.execute(
            "SELECT mastery FROM review_state WHERE question_id = ?", (question_id,)
        ).fetchone()
        mastery = (
            float(correct)
            if previous_mastery is None
            else round(previous_mastery[0] * 0.7 + float(correct) * 0.3, 6)
        )
        now = self._now()
        with transaction(self.connection):
            self.connection.execute(
                """
                INSERT INTO attempt_events(
                    id, session_id, question_id, answer, expected_answer, correct,
                    confidence, error_pattern, created_at, payload_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identifier,
                    session_id,
                    question_id,
                    answer,
                    expected_answer,
                    int(correct),
                    confidence,
                    pattern,
                    now.isoformat(),
                    payload_hash,
                ),
            )
            self.connection.execute(
                """
                INSERT INTO review_state(
                    question_id, next_review_at, interval_days,
                    consecutive_correct, mastery, last_attempt_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(question_id) DO UPDATE SET
                    next_review_at = excluded.next_review_at,
                    interval_days = excluded.interval_days,
                    consecutive_correct = excluded.consecutive_correct,
                    mastery = excluded.mastery,
                    last_attempt_at = excluded.last_attempt_at,
                    updated_at = excluded.updated_at
                """,
                (
                    question_id,
                    decision.next_review_at.isoformat(),
                    decision.interval_days,
                    decision.consecutive_confident_correct,
                    mastery,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        return self._grading_result(
            question_id,
            correct=correct,
            expected_answer=expected_answer,
            error_pattern=pattern,
        )

    def get_dashboard(self) -> dict[str, object]:
        total, correct = self.connection.execute(
            "SELECT count(*), coalesce(sum(correct), 0) FROM attempt_events"
        ).fetchone()
        review_total = self.connection.execute(
            "SELECT count(*) FROM review_state"
        ).fetchone()[0]
        due = self.connection.execute(
            "SELECT count(*) FROM review_state WHERE next_review_at <= ?",
            (self._now().isoformat(),),
        ).fetchone()[0]
        mastery = {
            row[0]: round(row[1], 4)
            for row in self.connection.execute(
                """
                SELECT question.discipline, avg(review.mastery)
                FROM review_state AS review
                JOIN questions AS question ON question.id = review.question_id
                GROUP BY question.discipline ORDER BY question.discipline
                """
            ).fetchall()
        }
        attempt_metrics = {
            row[0]: (row[1], row[2])
            for row in self.connection.execute(
                """
                SELECT question.discipline, count(*), coalesce(sum(attempt.correct), 0)
                FROM attempt_events AS attempt
                JOIN questions AS question ON question.id = attempt.question_id
                GROUP BY question.discipline
                """
            ).fetchall()
        }
        syllabus_metrics = {
            row[0]: (row[1], row[2])
            for row in self.connection.execute(
                """
                SELECT discipline, count(*), coalesce(sum(covered), 0)
                FROM syllabus_topics GROUP BY discipline
                """
            ).fetchall()
        }
        discipline_metrics = []
        for discipline in Discipline:
            discipline_attempts, discipline_correct = attempt_metrics.get(
                discipline.value, (0, 0)
            )
            topics_total, topics_covered = syllabus_metrics.get(
                discipline.value, (0, 0)
            )
            discipline_metrics.append(
                {
                    "discipline": discipline.value,
                    "mastery": mastery.get(discipline.value, 0.0),
                    "attempts": discipline_attempts,
                    "correct": discipline_correct,
                    "topics_total": topics_total,
                    "topics_covered": topics_covered,
                }
            )
        syllabus_total, syllabus_covered = self.connection.execute(
            "SELECT count(*), coalesce(sum(covered), 0) FROM syllabus_topics"
        ).fetchone()
        return {
            "attempts_total": total,
            "correct_total": correct,
            "accuracy": round(correct / total, 4) if total else 0.0,
            "review_total": review_total,
            "reviews_due": due,
            "mastery_by_discipline": mastery,
            "discipline_metrics": discipline_metrics,
            "error_patterns": [
                {"pattern": row[0], "count": row[1]}
                for row in self.connection.execute(
                    """
                    SELECT error_pattern, count(*) FROM attempt_events
                    WHERE error_pattern IS NOT NULL
                    GROUP BY error_pattern ORDER BY count(*) DESC, error_pattern
                    """
                ).fetchall()
            ],
            "syllabus": {
                "topics_total": syllabus_total,
                "topics_covered": syllabus_covered,
            },
            "study_bank": self.connection.execute(
                "SELECT count(*) FROM questions WHERE status = 'validated' AND relevance = 'direct'"
            ).fetchone()[0],
        }

    def get_review_queue(self) -> list[dict[str, object]]:
        return [
            {
                "question_id": row[0],
                "discipline": row[1],
                "topic_id": row[2],
                "next_review_at": row[3],
                "interval_days": row[4],
                "mastery": row[5],
                "due": row[3] <= self._now().isoformat(),
            }
            for row in self.connection.execute(
                """
                SELECT review.question_id, question.discipline, question.topic_id,
                       review.next_review_at, review.interval_days, review.mastery
                FROM review_state AS review
                JOIN questions AS question ON question.id = review.question_id
                ORDER BY review.next_review_at, review.question_id
                """
            ).fetchall()
        ]

    def export_canvas_summary(self) -> str:
        from pmal_study.reports import export_canvas_summary

        return export_canvas_summary(self.connection, self._now())
