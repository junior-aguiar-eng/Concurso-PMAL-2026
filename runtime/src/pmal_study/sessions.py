"""Serviço autoritativo de sessões, correção e progresso persistente."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Callable

from pmal_study.db import transaction
from pmal_study.generation import GenerationService
from pmal_study.models import (
    Discipline,
    EvidenceRef,
    ExemplarRecord,
    GradingResult,
    PreparedItem,
    PublicQuestion,
)
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

    @staticmethod
    def _json_value(value):
        if is_dataclass(value):
            return StudyService._json_value(asdict(value))
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, tuple):
            return [StudyService._json_value(item) for item in value]
        if isinstance(value, list):
            return [StudyService._json_value(item) for item in value]
        if isinstance(value, dict):
            return {str(key): StudyService._json_value(item) for key, item in value.items()}
        return value

    def _idempotent(self, operation_id: str, kind: str, request: dict, action):
        request_json = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        request_hash = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
        existing = self.connection.execute(
            "SELECT request_hash, response_json FROM operation_receipts WHERE id = ?",
            (operation_id,),
        ).fetchone()
        if existing:
            if existing[0] != request_hash:
                raise DomainError("idempotency_conflict", "A repetição diverge da operação original.")
            return json.loads(existing[1])
        with transaction(self.connection):
            result = self._json_value(action())
            self.connection.execute(
                "INSERT INTO operation_receipts(id, operation_kind, request_hash, response_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (operation_id, kind, request_hash, json.dumps(result, ensure_ascii=False, separators=(",", ":")), self._now().isoformat()),
            )
        return result

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
        evidence_parameters: list[object] = []
        evidence_scope = ""
        if selected:
            placeholders = ",".join("?" for _ in selected)
            evidence_scope = f" AND chunk.discipline IN ({placeholders})"
            evidence_parameters.extend(selected)
        due_clause = ""
        due_parameters: list[object] = []
        if mode == "review":
            due_clause = (
                " AND EXISTS (SELECT 1 FROM concept_review_state AS due "
                "WHERE due.learning_target_id = 'legacy:' || chunk.topic_id "
                "AND due.next_review_at <= ?)"
            )
            due_parameters.append(self._now().isoformat())
        has_evidence = self.connection.execute(
            f"""
            SELECT 1 FROM source_chunks AS chunk
            JOIN source_document_versions AS version ON version.id = chunk.version_id
            WHERE version.is_current = 1 AND chunk.status IN ('usable', 'ocr')
              AND chunk.topic_id IS NOT NULL {evidence_scope} {due_clause}
            LIMIT 1
            """,
            tuple((*evidence_parameters, *due_parameters)),
        ).fetchone() is not None
        if not self._eligible_ids(mode, selected) and not has_evidence:
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

    def _public_question(self, question_id: str) -> PublicQuestion:
        row = self.connection.execute(
            "SELECT id, discipline, topic_id, statement, origin FROM questions WHERE id = ?",
            (question_id,),
        ).fetchone()
        return PublicQuestion(
            id=row[0], discipline=Discipline(row[1]), topic_id=row[2],
            statement=row[3], origin_label=_ORIGIN_LABELS[row[4]],
        )

    def prepare_next_item(self, session_id: str) -> PreparedItem:
        mode, _, disciplines, _ = self._require_open_session(session_id)
        pending_question = self.connection.execute(
            """
            SELECT assigned.question_id
            FROM session_questions AS assigned
            WHERE assigned.session_id = ?
              AND NOT EXISTS (
                  SELECT 1 FROM attempt_events AS attempt
                  WHERE attempt.session_id = assigned.session_id
                    AND attempt.question_id = assigned.question_id
              )
            ORDER BY assigned.assigned_at LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        if pending_question:
            return PreparedItem("question", self._public_question(pending_question[0]), None)
        generator = GenerationService(self.connection, clock=self._clock)
        pending_job = self.connection.execute(
            "SELECT 1 FROM generation_jobs WHERE session_id = ? AND status = 'prepared' LIMIT 1",
            (session_id,),
        ).fetchone()
        if pending_job:
            return PreparedItem("generation", None, generator.prepare(session_id))
        attempted = {
            row[0] for row in self.connection.execute(
                "SELECT question_id FROM attempt_events WHERE session_id = ?", (session_id,)
            )
        }
        official = [item for item in self._eligible_ids(mode, disciplines) if item not in attempted]
        attempt_count = len(attempted)
        if mode != "review" and attempt_count % 3 == 2 and official:
            question = self._public_question(official[0])
            self.connection.execute(
                "INSERT OR IGNORE INTO session_questions(session_id, question_id, assigned_at) VALUES (?, ?, ?)",
                (session_id, question.id, self._now().isoformat()),
            )
            return PreparedItem("question", question, None)
        try:
            return PreparedItem("generation", None, generator.prepare(session_id))
        except Exception as error:
            from pmal_study.generation import GenerationError

            if not isinstance(error, GenerationError) or not official:
                raise
            question = self._public_question(official[0])
            self.connection.execute(
                "INSERT OR IGNORE INTO session_questions(session_id, question_id, assigned_at) VALUES (?, ?, ?)",
                (session_id, question.id, self._now().isoformat()),
            )
            return PreparedItem("question", question, None)

    def search_generation_evidence(self, job_id: str, query: str):
        return GenerationService(self.connection, clock=self._clock).search(job_id, query)

    def commit_generated_question(self, job_id: str, draft: dict[str, object]) -> PublicQuestion:
        return GenerationService(self.connection, clock=self._clock).commit(job_id, draft)

    @staticmethod
    def _host_job_public(row: sqlite3.Row | tuple) -> dict[str, object]:
        value: dict[str, object] = {
            "request_id": row[0],
            "kind": row[1],
            "status": row[2],
        }
        if row[3]:
            value["result"] = json.loads(row[3])
        if row[4]:
            value["error"] = {"code": row[4], "message": row[5]}
        return value

    def request_generation_job(self, generation_job_id: str) -> dict[str, object]:
        if not self.connection.execute(
            "SELECT 1 FROM generation_jobs WHERE id = ? AND status = 'prepared'",
            (generation_job_id,),
        ).fetchone():
            raise DomainError("generation_job_not_prepared", "Trabalho de geração indisponível.")
        existing = self.connection.execute(
            "SELECT id, kind, status, result_json, error_code, error_message "
            "FROM host_jobs WHERE generation_job_id = ?",
            (generation_job_id,),
        ).fetchone()
        if existing:
            return self._host_job_public(existing)
        request_id = str(uuid.uuid4())
        now = self._now().isoformat()
        payload_hash = hashlib.sha256(generation_job_id.encode("utf-8")).hexdigest()
        with transaction(self.connection):
            self.connection.execute(
                "INSERT INTO host_jobs(id, kind, generation_job_id, status, created_at, updated_at, payload_hash) "
                "VALUES (?, 'question_generation', ?, 'pending', ?, ?, ?)",
                (request_id, generation_job_id, now, now, payload_hash),
            )
        return {"request_id": request_id, "kind": "question_generation", "status": "pending"}

    def request_dissection_job(
        self, attempt_id: str, user_message: str, request_id: str,
    ) -> dict[str, object]:
        message = user_message.strip()
        if not message:
            raise DomainError("empty_dissection", "A pergunta de aprofundamento é obrigatória.")
        if not self.connection.execute(
            "SELECT 1 FROM attempt_events WHERE id = ?", (attempt_id,)
        ).fetchone():
            raise DomainError("attempt_not_found", "Tentativa não localizada.")
        request = {"attempt_id": attempt_id, "user_message": message}
        payload_hash = hashlib.sha256(
            json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        existing = self.connection.execute(
            "SELECT id, kind, status, result_json, error_code, error_message, payload_hash "
            "FROM host_jobs WHERE id = ?",
            (request_id,),
        ).fetchone()
        if existing:
            if existing[6] != payload_hash:
                raise DomainError("idempotency_conflict", "A repetição diverge da solicitação original.")
            return self._host_job_public(existing[:6])
        now = self._now().isoformat()
        with transaction(self.connection):
            self.connection.execute(
                "INSERT INTO host_jobs(id, kind, attempt_id, user_message, status, created_at, updated_at, payload_hash) "
                "VALUES (?, 'dissection', ?, ?, 'pending', ?, ?, ?)",
                (request_id, attempt_id, message, now, now, payload_hash),
            )
        return {"request_id": request_id, "kind": "dissection", "status": "pending"}

    def get_host_job_status(self, request_id: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT id, kind, status, result_json, error_code, error_message "
            "FROM host_jobs WHERE id = ?",
            (request_id,),
        ).fetchone()
        if row is None:
            raise DomainError("host_job_not_found", "Trabalho interno não localizado.")
        return self._host_job_public(row)

    def claim_host_job(self, request_id: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT kind, generation_job_id, attempt_id, user_message, status "
            "FROM host_jobs WHERE id = ?",
            (request_id,),
        ).fetchone()
        if row is None:
            raise DomainError("host_job_not_found", "Trabalho interno não localizado.")
        if row[4] in {"completed", "failed"}:
            raise DomainError("host_job_closed", "O trabalho interno já foi encerrado.")
        self.connection.execute(
            "UPDATE host_jobs SET status = 'processing', updated_at = ? WHERE id = ? AND status = 'pending'",
            (self._now().isoformat(), request_id),
        )
        if row[0] == "question_generation":
            brief = GenerationService(self.connection, clock=self._clock)._brief(row[1])
            return {
                "request_id": request_id,
                "kind": row[0],
                "generation_brief": self._json_value(brief),
            }
        attempt = self.connection.execute(
            "SELECT question_id, expected_answer, correct, error_pattern FROM attempt_events WHERE id = ?",
            (row[2],),
        ).fetchone()
        grading = self._grading_result(
            attempt[0], correct=bool(attempt[2]), expected_answer=attempt[1], error_pattern=attempt[3],
        )
        return {
            "request_id": request_id,
            "kind": row[0],
            "attempt_id": row[2],
            "question": self._json_value(self._public_question(attempt[0])),
            "grading": self._json_value(grading),
            "user_message": row[3],
            "conversation": self.get_dissection_exchanges(row[2]),
        }

    def complete_generation_job(
        self, request_id: str, draft: dict[str, object], model: str,
    ) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT generation_job_id, kind FROM host_jobs WHERE id = ?", (request_id,)
        ).fetchone()
        if row is None or row[1] != "question_generation":
            raise DomainError("wrong_host_job_kind", "O trabalho não é de geração de questão.")
        question = self.commit_generated_question(row[0], draft)
        result = self._json_value(question)
        now = self._now().isoformat()
        self.connection.execute(
            "UPDATE host_jobs SET status = 'completed', result_json = ?, error_code = NULL, "
            "error_message = NULL, updated_at = ? WHERE id = ?",
            (json.dumps(result, ensure_ascii=False, separators=(",", ":")), now, request_id),
        )
        return self.get_host_job_status(request_id)

    def complete_dissection_job(
        self, request_id: str, assistant_message: str, model: str,
    ) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT attempt_id, user_message, kind FROM host_jobs WHERE id = ?", (request_id,)
        ).fetchone()
        if row is None or row[2] != "dissection":
            raise DomainError("wrong_host_job_kind", "O trabalho não é de dissecação.")
        exchange = self.record_dissection_exchange(
            row[0], request_id, row[1], assistant_message, model,
        )
        now = self._now().isoformat()
        self.connection.execute(
            "UPDATE host_jobs SET status = 'completed', result_json = ?, error_code = NULL, "
            "error_message = NULL, updated_at = ? WHERE id = ?",
            (json.dumps(exchange, ensure_ascii=False, separators=(",", ":")), now, request_id),
        )
        return self.get_host_job_status(request_id)

    def fail_host_job(self, request_id: str, code: str, message: str) -> dict[str, object]:
        if not self.connection.execute(
            "SELECT 1 FROM host_jobs WHERE id = ?", (request_id,)
        ).fetchone():
            raise DomainError("host_job_not_found", "Trabalho interno não localizado.")
        self.connection.execute(
            "UPDATE host_jobs SET status = 'failed', error_code = ?, error_message = ?, updated_at = ? "
            "WHERE id = ?",
            (code, message, self._now().isoformat(), request_id),
        )
        return self.get_host_job_status(request_id)

    def _session_payload(self, session_id: str) -> dict[str, object]:
        mode, duration, disciplines, started_at, status = self._session(session_id)
        return {
            "session_id": session_id,
            "mode": mode,
            "duration_minutes": duration,
            "disciplines": list(disciplines),
            "started_at": started_at.isoformat(),
            "status": status,
        }

    def public_prepared_item(self, prepared: PreparedItem) -> dict[str, object]:
        if prepared.question:
            return {
                "kind": "question",
                "question": self._json_value(prepared.question),
                "generation_request": None,
            }
        if prepared.generation_brief:
            return {
                "kind": "generation",
                "question": None,
                "generation_request": self.request_generation_job(prepared.generation_brief.job_id),
            }
        return {"kind": "terminal", "question": None, "generation_request": None}

    def open_study_panel(
        self, *, mode: str | None = None, duration_minutes: int | None = None,
        disciplines: list[str] | None = None, operation_id: str | None = None,
    ) -> dict[str, object]:
        request = {"mode": mode, "duration_minutes": duration_minutes, "disciplines": disciplines}

        def open_or_resume() -> dict[str, object]:
            row = self.connection.execute(
                "SELECT id FROM study_sessions WHERE status = 'open' ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if mode is not None:
                session = self.start_session(mode, duration_minutes, disciplines)
                session_id = session.id
                self.connection.execute(
                    "UPDATE study_sessions SET status = 'completed', ended_at = ? "
                    "WHERE status = 'open' AND id != ?",
                    (self._now().isoformat(), session_id),
                )
            elif row:
                session_id = row[0]
                try:
                    self._require_open_session(session_id)
                except DomainError as error:
                    if error.code != "session_expired":
                        raise
                    return {
                        "phase": "configuration", "session": None, "question": None,
                        "conversation": [], "dashboard": self.get_dashboard(),
                    }
            else:
                return {"phase": "configuration", "session": None, "question": None, "conversation": [], "dashboard": self.get_dashboard()}
            last_attempt = self.connection.execute(
                "SELECT id, question_id, expected_answer, correct, error_pattern FROM attempt_events "
                "WHERE session_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1", (session_id,)
            ).fetchone()
            if last_attempt:
                grading = self._grading_result(
                    last_attempt[1], correct=bool(last_attempt[3]),
                    expected_answer=last_attempt[2], error_pattern=last_attempt[4],
                )
                try:
                    upcoming = self.prefetch_next_item(session_id, last_attempt[1])
                    next_item = self.public_prepared_item(upcoming)
                except DomainError as error:
                    if error.code != "session_complete":
                        raise
                    next_item = {"kind": "terminal", "question": None, "generation_request": None}
                conversation = self.get_dissection_exchanges(last_attempt[0])
                return {
                    "phase": "dissection" if conversation else "correction",
                    "session": self._session_payload(session_id),
                    "question": self._json_value(self._public_question(last_attempt[1])),
                    "generation_request": None,
                    "attempt_id": last_attempt[0], "grading": self._json_value(grading),
                    "next_item": next_item, "conversation": conversation,
                    "dashboard": self.get_dashboard(),
                }
            prepared = self.prepare_next_item(session_id)
            question = self._json_value(prepared.question) if prepared.question else None
            return {
                "phase": "question" if question else "generation",
                "session": self._session_payload(session_id),
                "question": question,
                "generation_request": (
                    self.request_generation_job(prepared.generation_brief.job_id)
                    if prepared.generation_brief else None
                ),
                "conversation": [],
                "dashboard": self.get_dashboard(),
            }

        return self._idempotent(operation_id, "open_study_panel", request, open_or_resume) if operation_id else open_or_resume()

    def prefetch_next_item(self, session_id: str, current_question_id: str) -> PreparedItem:
        mode, _, disciplines, _ = self._require_open_session(session_id)
        buffered = self.connection.execute(
            """
            SELECT assigned.question_id FROM session_questions AS assigned
            WHERE assigned.session_id = ? AND assigned.question_id <> ?
              AND NOT EXISTS (SELECT 1 FROM attempt_events AS attempt
                              WHERE attempt.session_id = assigned.session_id
                                AND attempt.question_id = assigned.question_id)
            ORDER BY assigned.assigned_at LIMIT 1
            """,
            (session_id, current_question_id),
        ).fetchone()
        if buffered:
            return PreparedItem("question", self._public_question(buffered[0]), None)
        pending_job = self.connection.execute(
            "SELECT id FROM generation_jobs WHERE session_id = ? AND status = 'prepared' ORDER BY prepared_at LIMIT 1",
            (session_id,),
        ).fetchone()
        if pending_job:
            return PreparedItem("generation", None, GenerationService(self.connection, clock=self._clock)._brief(pending_job[0]))
        excluded = {current_question_id}
        excluded.update(row[0] for row in self.connection.execute(
            "SELECT question_id FROM attempt_events WHERE session_id = ?", (session_id,)
        ))
        available = [identifier for identifier in self._eligible_ids(mode, disciplines) if identifier not in excluded]
        if available:
            question = self._public_question(available[0])
            self.connection.execute(
                "INSERT OR IGNORE INTO session_questions(session_id, question_id, assigned_at) VALUES (?, ?, ?)",
                (session_id, question.id, self._now().isoformat()),
            )
            return PreparedItem("question", question, None)
        try:
            return PreparedItem("generation", None, GenerationService(self.connection, clock=self._clock).prepare(session_id))
        except Exception as error:
            from pmal_study.generation import GenerationError
            if isinstance(error, GenerationError):
                raise DomainError("session_complete", "Não há outra questão disponível nesta sessão.") from error
            raise

    def submit_and_prepare(
        self, session_id: str, question_id: str, answer: str, confidence: int,
        attempt_id: str, error_pattern: str | None = None,
    ) -> dict[str, object]:
        request = {
            "session_id": session_id, "question_id": question_id, "answer": answer,
            "confidence": confidence, "error_pattern": error_pattern,
        }

        def submit() -> dict[str, object]:
            before_attempts, before_correct = self.connection.execute(
                "SELECT count(*), coalesce(sum(correct), 0) FROM attempt_events"
            ).fetchone()
            grading = self.submit_answer(
                session_id, question_id, answer, confidence,
                attempt_id=attempt_id, error_pattern=error_pattern,
            )
            after_attempts, after_correct = self.connection.execute(
                "SELECT count(*), coalesce(sum(correct), 0) FROM attempt_events"
            ).fetchone()
            try:
                next_item = self.prefetch_next_item(session_id, question_id)
                next_payload = self.public_prepared_item(next_item)
            except DomainError as error:
                if error.code != "session_complete":
                    raise
                next_payload = {"kind": "terminal", "question": None, "generation_request": None}
            due = self.connection.execute(
                "SELECT count(*) FROM concept_review_state WHERE next_review_at <= ?",
                (self._now().isoformat(),),
            ).fetchone()[0]
            return {
                "attempt_id": attempt_id,
                "grading": self._json_value(grading),
                "session_summary": self.session_summary(session_id),
                "metric_deltas": {"attempts": after_attempts - before_attempts, "correct": after_correct - before_correct, "reviews_due": due},
                "next_item": next_payload,
            }

        return self._idempotent(attempt_id, "submit_and_prepare", request, submit)

    def record_dissection_exchange(
        self, attempt_id: str, exchange_id: str, user_message: str,
        assistant_message: str, model: str,
    ) -> dict[str, object]:
        if not user_message.strip() or not assistant_message.strip():
            raise DomainError("empty_exchange", "Pergunta e resposta são obrigatórias.")
        if not self.connection.execute("SELECT 1 FROM attempt_events WHERE id = ?", (attempt_id,)).fetchone():
            raise DomainError("attempt_not_found", "Tentativa não localizada.")
        payload = {"attempt_id": attempt_id, "user_message": user_message, "assistant_message": assistant_message, "model": model}
        payload_hash = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        existing = self.connection.execute(
            "SELECT attempt_id, sequence, user_message, assistant_message, model, created_at, payload_hash "
            "FROM dissection_exchanges WHERE id = ?", (exchange_id,)
        ).fetchone()
        if existing:
            if existing[6] != payload_hash:
                raise DomainError("idempotency_conflict", "A repetição diverge da troca original.")
            return {"exchange_id": exchange_id, "attempt_id": existing[0], "sequence": existing[1], "user_message": existing[2], "assistant_message": existing[3], "model": existing[4], "created_at": existing[5]}
        sequence = self.connection.execute(
            "SELECT coalesce(max(sequence), 0) + 1 FROM dissection_exchanges WHERE attempt_id = ?", (attempt_id,)
        ).fetchone()[0]
        created_at = self._now().isoformat()
        self.connection.execute(
            "INSERT INTO dissection_exchanges(id, attempt_id, sequence, user_message, assistant_message, model, created_at, payload_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (exchange_id, attempt_id, sequence, user_message.strip(), assistant_message.strip(), model, created_at, payload_hash),
        )
        return {"exchange_id": exchange_id, "attempt_id": attempt_id, "sequence": sequence, "user_message": user_message.strip(), "assistant_message": assistant_message.strip(), "model": model, "created_at": created_at}

    def get_dissection_exchanges(self, attempt_id: str) -> list[dict[str, object]]:
        return [
            {"exchange_id": row[0], "attempt_id": attempt_id, "sequence": row[1], "user_message": row[2], "assistant_message": row[3], "model": row[4], "created_at": row[5]}
            for row in self.connection.execute(
                "SELECT id, sequence, user_message, assistant_message, model, created_at "
                "FROM dissection_exchanges WHERE attempt_id = ? ORDER BY sequence", (attempt_id,)
            )
        ]

    def session_summary(self, session_id: str) -> dict[str, object]:
        attempts, correct = self.connection.execute(
            "SELECT count(*), coalesce(sum(correct), 0) FROM attempt_events WHERE session_id = ?", (session_id,)
        ).fetchone()
        return {"session_id": session_id, "attempts": attempts, "correct": correct, "accuracy": correct / attempts if attempts else 0.0}

    def end_session(self, session_id: str, operation_id: str) -> dict[str, object]:
        request = {"session_id": session_id}

        def end() -> dict[str, object]:
            _, _, _, _, status = self._session(session_id)
            if status == "open":
                self.connection.execute(
                    "UPDATE study_sessions SET status = 'completed', ended_at = ? WHERE id = ?",
                    (self._now().isoformat(), session_id),
                )
            return {**self.session_summary(session_id), "status": "completed", "ended_at": self._now().isoformat()}

        return self._idempotent(operation_id, "end_session", request, end)

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
        scoped = self.connection.execute(
            """
            SELECT question.id
            FROM session_questions AS assigned
            JOIN questions AS question ON question.id = assigned.question_id
            WHERE assigned.session_id = ?
              AND NOT EXISTS (
                  SELECT 1 FROM attempt_events AS attempt
                  WHERE attempt.session_id = assigned.session_id
                    AND attempt.question_id = assigned.question_id
              )
            ORDER BY assigned.assigned_at
            """,
            (session_id,),
        ).fetchone()
        available = [
            identifier
            for identifier in self._eligible_ids(mode, disciplines)
            if identifier not in attempted
        ]
        if scoped is not None:
            available.insert(0, scoped[0])
        if not available:
            discipline_clause = ""
            parameters: list[object] = []
            if disciplines:
                placeholders = ",".join("?" for _ in disciplines)
                discipline_clause = f" AND chunk.discipline IN ({placeholders})"
                parameters.extend(disciplines)
            if self.connection.execute(
                f"""
                SELECT 1 FROM source_chunks AS chunk
                JOIN source_document_versions AS version ON version.id = chunk.version_id
                WHERE version.is_current = 1 AND chunk.status IN ('usable', 'ocr')
                  AND chunk.topic_id IS NOT NULL {discipline_clause}
                LIMIT 1
                """,
                tuple(parameters),
            ).fetchone():
                raise DomainError(
                    "generation_required",
                    "A próxima questão deve ser gerada a partir das evidências preparadas.",
                )
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
            "SELECT rationale, decisive_expression, trap, distinction FROM questions WHERE id = ?", (question_id,)
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
        target = self._learning_target(question_id)
        review = (
            self.connection.execute(
                "SELECT next_review_at FROM concept_review_state WHERE learning_target_id = ?",
                (target,),
            ).fetchone()
            if target else None
        ) or self.connection.execute(
            "SELECT next_review_at FROM review_state WHERE question_id = ?", (question_id,)
        ).fetchone()
        evidence_rows = self.connection.execute(
            """
            SELECT evidence_kind, coalesce(source_chunk_id, live_snapshot_id), locator,
                   excerpt, evidence_hash, authority
            FROM question_evidence WHERE question_id = ? ORDER BY claim_key
            """,
            (question_id,),
        ).fetchall()
        evidence = tuple(
            EvidenceRef(
                id=row[1], kind=row[0], source=(source[0] if source else "fonte registrada"),
                page=(source[1] if source and row[0] == "local" else None),
                url=(source[0] if source and row[0] == "live" else None),
                locator=row[2], excerpt=row[3], sha256=row[4], authority=row[5],
            )
            for row in evidence_rows
        )
        return GradingResult(
            correct=correct,
            expected_answer=expected_answer,
            rationale=question[0],
            source_label=source[0] if source else "fonte não vinculada",
            source_page=source[1] if source else 0,
            next_review_at=_parse_time(review[0]),
            error_pattern=error_pattern,
            analysis=question[0],
            decisive_expression=question[1] or "núcleo normativo indicado na fundamentação",
            trap=question[2] or "generalização ou inversão do alcance da regra",
            distinction=question[3] or question[0],
            evidence=evidence,
        )

    def _learning_target(self, question_id: str) -> str | None:
        row = self.connection.execute(
            "SELECT discipline, topic_id, concept_key FROM questions WHERE id = ?",
            (question_id,),
        ).fetchone()
        if row is None:
            return None
        concept_key = row[2] or (f"legacy:{row[1]}" if row[1] else None)
        if concept_key is None:
            return None
        target = self.connection.execute(
            "SELECT id FROM learning_targets WHERE discipline = ? AND concept_key = ?",
            (row[0], concept_key),
        ).fetchone()
        return str(target[0]) if target else None

    def _validate_question_evidence(self, question_id: str) -> None:
        count = self.connection.execute(
            "SELECT count(*) FROM question_evidence WHERE question_id = ?", (question_id,)
        ).fetchone()[0]
        if count == 0:
            return
        valid = self.connection.execute(
            """
            SELECT count(*)
            FROM question_evidence AS evidence
            LEFT JOIN source_chunks AS chunk ON chunk.id = evidence.source_chunk_id
            LEFT JOIN source_document_versions AS version ON version.id = chunk.version_id
            LEFT JOIN live_source_snapshots AS live ON live.id = evidence.live_snapshot_id
            WHERE evidence.question_id = ? AND (
                (evidence.evidence_kind = 'local' AND chunk.status IN ('usable', 'ocr') AND version.is_current = 1)
                OR (evidence.evidence_kind = 'live' AND live.status IN ('verified', 'changed'))
            )
            """,
            (question_id,),
        ).fetchone()[0]
        if valid != count:
            raise DomainError(
                "evidence_invalidated",
                "A correção foi suspensa porque uma evidência foi invalidada.",
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
        scoped_question = self.connection.execute(
            "SELECT 1 FROM session_questions WHERE session_id = ? AND question_id = ?",
            (session_id, question_id),
        ).fetchone() is not None
        if question_id not in self._eligible_ids(mode, disciplines) and not scoped_question:
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
        self._validate_question_evidence(question_id)
        correct = answer == expected_answer
        pattern = error_pattern or (None if correct else "incorrect_judgment")
        learning_target_id = self._learning_target(question_id)
        prior_attempts = [
            PriorAttempt(correct=bool(row[0]), confidence=row[1])
            for row in self.connection.execute(
                """
                SELECT correct, confidence FROM attempt_events
                WHERE (? IS NOT NULL AND learning_target_id = ?)
                   OR (? IS NULL AND question_id = ?)
                ORDER BY created_at, id
                """,
                (learning_target_id, learning_target_id, learning_target_id, question_id),
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
                    confidence, error_pattern, created_at, payload_hash,
                    learning_target_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    learning_target_id,
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
            if learning_target_id is not None:
                previous_concept = self.connection.execute(
                    "SELECT mastery FROM concept_review_state WHERE learning_target_id = ?",
                    (learning_target_id,),
                ).fetchone()
                concept_mastery = (
                    float(correct) if previous_concept is None
                    else round(previous_concept[0] * 0.7 + float(correct) * 0.3, 6)
                )
                self.connection.execute(
                    """
                    INSERT INTO concept_review_state(
                        learning_target_id, next_review_at, interval_days,
                        consecutive_correct, mastery, last_attempt_at, updated_at,
                        last_question_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(learning_target_id) DO UPDATE SET
                        next_review_at = excluded.next_review_at,
                        interval_days = excluded.interval_days,
                        consecutive_correct = excluded.consecutive_correct,
                        mastery = excluded.mastery,
                        last_attempt_at = excluded.last_attempt_at,
                        updated_at = excluded.updated_at,
                        last_question_id = excluded.last_question_id
                    """,
                    (
                        learning_target_id, decision.next_review_at.isoformat(),
                        decision.interval_days, decision.consecutive_confident_correct,
                        concept_mastery, now.isoformat(), now.isoformat(), question_id,
                    ),
                )
            self.connection.execute(
                """
                UPDATE syllabus_topics SET covered = 1
                WHERE id = (SELECT topic_id FROM questions WHERE id = ?)
                """,
                (question_id,),
            )
        return self._grading_result(
            question_id,
            correct=correct,
            expected_answer=expected_answer,
            error_pattern=pattern,
        )

    def set_exemplar(
        self,
        question_id: str,
        action: str,
        *,
        actor: str = "user",
        reason: str | None = None,
    ) -> ExemplarRecord:
        if action not in {"approve", "revoke"}:
            raise DomainError("invalid_exemplar_action", "A ação deve ser approve ou revoke.")
        question = self.connection.execute(
            "SELECT 1 FROM questions WHERE id = ?", (question_id,)
        ).fetchone()
        if question is None:
            raise DomainError("question_not_found", "Questão não localizada.")
        if not self.connection.execute(
            "SELECT 1 FROM attempt_events WHERE question_id = ? LIMIT 1", (question_id,)
        ).fetchone():
            raise DomainError(
                "question_not_corrected",
                "A questão só pode virar exemplar depois de respondida e corrigida.",
            )
        existing = self.connection.execute(
            "SELECT state, approved_at FROM approved_exemplars WHERE question_id = ?",
            (question_id,),
        ).fetchone()
        if action == "revoke" and (existing is None or existing[0] != "approved"):
            raise DomainError("exemplar_not_approved", "A questão não é exemplar aprovado.")
        now = self._now().isoformat()
        approved_at = now if action == "approve" else existing[1]
        revoked_at = now if action == "revoke" else None
        state = "approved" if action == "approve" else "revoked"
        with transaction(self.connection):
            self.connection.execute(
                """
                INSERT INTO approved_exemplars(
                    question_id, state, approved_at, revoked_at, event_actor, reason
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(question_id) DO UPDATE SET
                    state = excluded.state,
                    approved_at = CASE WHEN excluded.state = 'approved'
                                       THEN excluded.approved_at ELSE approved_exemplars.approved_at END,
                    revoked_at = excluded.revoked_at,
                    event_actor = excluded.event_actor,
                    reason = excluded.reason
                """,
                (question_id, state, approved_at, revoked_at, actor, reason),
            )
            self.connection.execute(
                "INSERT INTO exemplar_events(id, question_id, action, actor, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), question_id, action, actor, reason, now),
            )
        return ExemplarRecord(
            question_id=question_id, state=state, approved_at=approved_at,
            revoked_at=revoked_at, event_actor=actor, reason=reason,
        )

    def get_dashboard(self) -> dict[str, object]:
        total, correct = self.connection.execute(
            "SELECT count(*), coalesce(sum(correct), 0) FROM attempt_events"
        ).fetchone()
        review_total = self.connection.execute(
            "SELECT count(*) FROM concept_review_state"
        ).fetchone()[0]
        due = self.connection.execute(
            "SELECT count(*) FROM concept_review_state WHERE next_review_at <= ?",
            (self._now().isoformat(),),
        ).fetchone()[0]
        mastery = {
            row[0]: round(row[1], 4)
            for row in self.connection.execute(
                """
                SELECT target.discipline, avg(review.mastery)
                FROM concept_review_state AS review
                JOIN learning_targets AS target ON target.id = review.learning_target_id
                GROUP BY target.discipline ORDER BY target.discipline
                """
            ).fetchall()
        }
        concept_metrics = [
            {
                "learning_target_id": row[0], "discipline": row[1],
                "topic_id": row[2], "concept_key": row[3],
                "mastery": round(row[4], 4), "next_review_at": row[5],
                "due": row[5] <= self._now().isoformat(),
            }
            for row in self.connection.execute(
                """
                SELECT target.id, target.discipline, target.topic_id,
                       target.concept_key, review.mastery, review.next_review_at
                FROM concept_review_state AS review
                JOIN learning_targets AS target ON target.id = review.learning_target_id
                ORDER BY review.next_review_at, target.discipline, target.topic_id
                """
            ).fetchall()
        ]
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
        concepts_tracked, concepts_mastered = self.connection.execute(
            "SELECT count(*), coalesce(sum(mastery >= .8), 0) FROM concept_review_state"
        ).fetchone()
        corpus = self.connection.execute(
            """
            SELECT
                (SELECT count(*) FROM source_documents),
                count(*), sum(page_state = 'usable'), sum(page_state = 'ocr'),
                sum(page_state = 'quarantined'),
                (SELECT count(*) FROM source_documents WHERE status = 'extraction_failed')
                  + (SELECT count(*) FROM live_source_snapshots WHERE status = 'invalidated')
            FROM source_pages
            """
        ).fetchone()
        applied = {
            row[0]: row[1]
            for row in self.connection.execute(
                """
                SELECT CASE WHEN question.origin = 'original' THEN 'generated' ELSE 'official' END,
                       count(DISTINCT attempt.question_id)
                FROM attempt_events AS attempt
                JOIN questions AS question ON question.id = attempt.question_id
                GROUP BY 1
                """
            ).fetchall()
        }
        exemplar_records = [
            {
                "question_id": row[0], "state": row[1], "discipline": row[2],
                "topic_id": row[3], "approved_at": row[4],
            }
            for row in self.connection.execute(
                """
                SELECT exemplar.question_id, exemplar.state, question.discipline,
                       question.topic_id, exemplar.approved_at
                FROM approved_exemplars AS exemplar
                JOIN questions AS question ON question.id = exemplar.question_id
                WHERE exemplar.state = 'approved'
                ORDER BY exemplar.approved_at DESC
                """
            ).fetchall()
        ]
        exemplar_candidates = [
            {
                "question_id": row[0], "discipline": row[1],
                "topic_id": row[2], "statement": row[3],
            }
            for row in self.connection.execute(
                """
                SELECT question.id, question.discipline, question.topic_id, question.statement
                FROM questions AS question
                WHERE question.origin = 'original'
                  AND EXISTS (SELECT 1 FROM attempt_events WHERE question_id = question.id)
                  AND NOT EXISTS (
                      SELECT 1 FROM approved_exemplars
                      WHERE question_id = question.id AND state = 'approved'
                  )
                ORDER BY question.validated_at DESC LIMIT 20
                """
            ).fetchall()
        ]
        average_confidence, high_confidence_errors = self.connection.execute(
            "SELECT coalesce(avg(confidence), 0), coalesce(sum(confidence >= 2 AND correct = 0), 0) FROM attempt_events"
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
            "concepts": {
                "tracked": concepts_tracked,
                "mastered": concepts_mastered,
                "reviews_due": due,
            },
            "concept_metrics": concept_metrics,
            "corpus": {
                "documents": corpus[0] or 0,
                "total_pages": corpus[1] or 0,
                "usable_pages": corpus[2] or 0,
                "ocr_pages": corpus[3] or 0,
                "quarantined_pages": corpus[4] or 0,
                "stale_sources": corpus[5] or 0,
            },
            "questions_applied": {
                "official": applied.get("official", 0),
                "generated": applied.get("generated", 0),
            },
            "exemplars": {
                "approved": len(exemplar_records),
                "records": exemplar_records,
            },
            "exemplar_candidates": exemplar_candidates,
            "confidence": {
                "average": round(float(average_confidence), 2),
                "high_confidence_errors": high_confidence_errors,
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
                SELECT review.last_question_id, target.discipline, target.topic_id,
                       review.next_review_at, review.interval_days, review.mastery
                FROM concept_review_state AS review
                JOIN learning_targets AS target ON target.id = review.learning_target_id
                ORDER BY review.next_review_at, target.id
                """
            ).fetchall()
        ]

    def export_canvas_summary(self) -> str:
        from pmal_study.reports import export_canvas_summary

        return export_canvas_summary(self.connection, self._now())
