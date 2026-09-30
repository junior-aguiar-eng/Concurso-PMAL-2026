"""Preparação, validação e congelamento de questões geradas pelo modelo hospedeiro."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any, Callable

from pmal_study.db import transaction
from pmal_study.evidence import search_evidence
from pmal_study.models import Discipline
from pmal_study.models import (
    Discipline,
    EvidenceRef,
    ExemplarReference,
    GeneratedQuestionDraft,
    GenerationBrief,
    PublicQuestion,
)


class GenerationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _parse_draft(payload: dict[str, Any]) -> GeneratedQuestionDraft:
    required = (
        "statement", "answer", "rationale", "construction_pattern", "difficulty",
        "concept", "decisive_expression", "trap", "distinction", "evidence_links",
    )
    missing = [field for field in required if field not in payload]
    if missing:
        raise GenerationError("invalid_draft", f"Campos ausentes: {', '.join(missing)}")
    if payload["answer"] not in {"C", "E"}:
        raise GenerationError("invalid_answer", "O gabarito deve ser C ou E.")
    if not isinstance(payload["difficulty"], int) or payload["difficulty"] not in range(1, 6):
        raise GenerationError("invalid_difficulty", "A dificuldade deve estar entre 1 e 5.")
    links = payload["evidence_links"]
    if not isinstance(links, list) or not links:
        raise GenerationError("missing_evidence", "A questão exige ao menos uma evidência.")
    normalized_links: list[dict[str, str]] = []
    for link in links:
        if not isinstance(link, dict) or not link.get("evidence_id") or not link.get("claim_key"):
            raise GenerationError("invalid_evidence", "Vínculo de evidência inválido.")
        normalized_links.append(
            {"evidence_id": str(link["evidence_id"]), "claim_key": str(link["claim_key"])}
        )
    draft = GeneratedQuestionDraft(
        statement=str(payload["statement"]).strip(),
        answer=str(payload["answer"]),
        rationale=str(payload["rationale"]).strip(),
        construction_pattern=str(payload["construction_pattern"]).strip(),
        difficulty=int(payload["difficulty"]),
        concept=str(payload["concept"]).strip(),
        decisive_expression=str(payload["decisive_expression"]).strip(),
        trap=str(payload["trap"]).strip(),
        distinction=str(payload["distinction"]).strip(),
        evidence_links=tuple(normalized_links),
    )
    textual = (
        draft.statement, draft.rationale, draft.construction_pattern, draft.concept,
        draft.decisive_expression, draft.trap, draft.distinction,
    )
    if any(not value for value in textual) or len(draft.statement) < 40 or len(draft.rationale) < 30:
        raise GenerationError("insufficient_draft", "Questão ou fundamentação formalmente insuficiente.")
    return draft


class GenerationService:
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

    def _open_session(self, session_id: str) -> tuple[str, tuple[str, ...]]:
        row = self.connection.execute(
            "SELECT mode, disciplines_json, status FROM study_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None or row[2] != "open":
            raise GenerationError("session_not_open", "A sessão não existe ou não está aberta.")
        return str(row[0]), tuple(json.loads(row[1] or "[]"))

    def _evidence_for_job(self, job_id: str) -> tuple[EvidenceRef, ...]:
        local = self.connection.execute(
            """
            SELECT chunk.id, document.relative_path, chunk.page_start, chunk.locator,
                   chunk.text, chunk.sha256, chunk.authority
            FROM generation_evidence AS link
            JOIN source_chunks AS chunk ON chunk.id = link.source_chunk_id
            JOIN source_documents AS document ON document.id = chunk.document_id
            WHERE link.job_id = ? AND link.evidence_kind = 'local'
            ORDER BY link.rank
            """,
            (job_id,),
        ).fetchall()
        live = self.connection.execute(
            """
            SELECT snapshot.id, snapshot.final_url, snapshot.locator,
                   snapshot.confirmed_excerpt, snapshot.sha256, snapshot.authority,
                   snapshot.retrieved_at
            FROM generation_evidence AS link
            JOIN live_source_snapshots AS snapshot ON snapshot.id = link.live_snapshot_id
            WHERE link.job_id = ? AND link.evidence_kind = 'live'
            ORDER BY link.rank
            """,
            (job_id,),
        ).fetchall()
        values = [
            EvidenceRef(
                id=row[0], kind="local", source=row[1], page=row[2], locator=row[3] or f"p. {row[2]}",
                excerpt=row[4][:2400], sha256=row[5], authority=row[6],
            )
            for row in local
        ]
        values.extend(
            EvidenceRef(
                id=row[0], kind="live", source=row[1], url=row[1], locator=row[2],
                excerpt=row[3], sha256=row[4], authority=row[5], retrieved_at=row[6],
            )
            for row in live
        )
        return tuple(values)

    def _brief(self, job_id: str) -> GenerationBrief:
        row = self.connection.execute(
            """
            SELECT session_id, discipline, topic_id, concept_key, difficulty,
                   pedagogical_reason, update_policy
            FROM generation_jobs WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            raise GenerationError("job_not_found", "Trabalho de geração não localizado.")
        exemplars = tuple(
            ExemplarReference(
                question_id=item[0], statement=item[1], construction_pattern=item[2] or "",
                difficulty=item[3] or 3, trap=item[4] or "",
            )
            for item in self.connection.execute(
                """
                SELECT question.id, question.statement, question.construction_pattern,
                       question.difficulty, question.trap
                FROM approved_exemplars AS exemplar
                JOIN questions AS question ON question.id = exemplar.question_id
                WHERE exemplar.state = 'approved' AND question.discipline = ?
                  AND (question.topic_id = ? OR question.concept_key = ?)
                ORDER BY abs(coalesce(question.difficulty, 3) - ?), exemplar.approved_at DESC
                LIMIT 3
                """,
                (row[1], row[2], row[3], row[4]),
            ).fetchall()
        )
        return GenerationBrief(
            job_id=job_id, session_id=row[0], discipline=Discipline(row[1]),
            topic_id=row[2], concept_key=row[3], difficulty=row[4],
            pedagogical_reason=row[5], evidence=self._evidence_for_job(job_id),
            exemplars=exemplars, update_policy=row[6],
        )

    def prepare(self, session_id: str) -> GenerationBrief:
        mode, disciplines = self._open_session(session_id)
        pending = self.connection.execute(
            "SELECT id FROM generation_jobs WHERE session_id = ? AND status = 'prepared' "
            "ORDER BY prepared_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if pending:
            return self._brief(pending[0])
        allowed = disciplines or tuple(item.value for item in Discipline)
        placeholders = ",".join("?" for _ in allowed)
        review_clause = " AND review.next_review_at <= ?" if mode == "review" else ""
        target_parameters: tuple[object, ...] = (*allowed, self._now().isoformat()) if mode == "review" else allowed
        targets = self.connection.execute(
            f"""
            SELECT target.id, target.discipline, target.topic_id, target.concept_key,
                   target.title, target.historical_frequency, review.mastery
            FROM learning_targets AS target
            LEFT JOIN concept_review_state AS review ON review.learning_target_id = target.id
            WHERE target.active = 1 AND target.discipline IN ({placeholders})
              AND target.topic_id IS NOT NULL
              {review_clause}
              AND EXISTS (
                  SELECT 1 FROM source_chunks AS chunk
                  JOIN source_document_versions AS version ON version.id = chunk.version_id
                  WHERE chunk.topic_id = target.topic_id AND version.is_current = 1
                    AND chunk.status IN ('usable', 'ocr')
              )
            ORDER BY review.mastery IS NOT NULL, coalesce(review.mastery, 0),
                     target.historical_frequency DESC, target.id
            """,
            target_parameters,
        ).fetchall()
        for target in targets:
            evidence = search_evidence(
                self.connection, discipline=target[1], topic_id=target[2],
                query=target[4], limit=8,
            )
            if evidence:
                break
        else:
            raise GenerationError("no_evidence", "Não há evidência suficiente no escopo da sessão.")
        difficulty = 4 if target[6] is None else max(2, min(5, round(3 + float(target[6]) * 2)))
        reason = "conceito ainda não avaliado" if target[6] is None else "reforço orientado pelo domínio conceitual"
        # Conteúdo histórico-geográfico de Alagoas não sofre alteração normativa;
        # a confirmação ao vivo existe para capturar atualização legislativa.
        update_policy = (
            "local_sufficient"
            if target[1] == Discipline.CONHECIMENTOS_ALAGOAS.value
            or any(item.authority in {"official_legislation", "local_official_copy", "official_court"} for item in evidence)
            else "selective_live_confirmation"
        )
        job_id = str(uuid.uuid4())
        now = self._now().isoformat()
        with transaction(self.connection):
            self.connection.execute(
                """
                INSERT INTO generation_jobs(
                    id, session_id, discipline, topic_id, concept_key, difficulty,
                    pedagogical_reason, update_policy, status, prepared_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'prepared', ?)
                """,
                (job_id, session_id, target[1], target[2], target[3], difficulty, reason, update_policy, now),
            )
            self.connection.executemany(
                "INSERT INTO generation_evidence(job_id, evidence_kind, source_chunk_id, rank) "
                "VALUES (?, 'local', ?, ?)",
                ((job_id, item.id, rank) for rank, item in enumerate(evidence, start=1)),
            )
        return self._brief(job_id)

    def search(self, job_id: str, query: str) -> tuple[EvidenceRef, ...]:
        row = self.connection.execute(
            "SELECT discipline, topic_id, status FROM generation_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None or row[2] != "prepared":
            raise GenerationError("job_not_prepared", "Trabalho de geração indisponível para busca.")
        evidence = search_evidence(
            self.connection, discipline=row[0], topic_id=row[1], query=query, limit=8,
        )
        existing = {
            value[0] for value in self.connection.execute(
                "SELECT source_chunk_id FROM generation_evidence WHERE job_id = ? AND source_chunk_id IS NOT NULL",
                (job_id,),
            )
        }
        rank = self.connection.execute(
            "SELECT coalesce(max(rank), 0) FROM generation_evidence WHERE job_id = ?",
            (job_id,),
        ).fetchone()[0]
        with transaction(self.connection):
            for item in evidence:
                if item.id in existing:
                    continue
                rank += 1
                self.connection.execute(
                    "INSERT INTO generation_evidence(job_id, evidence_kind, source_chunk_id, rank) "
                    "VALUES (?, 'local', ?, ?)",
                    (job_id, item.id, rank),
                )
        return evidence

    def attach_live(self, job_id: str, snapshot_id: str) -> None:
        job = self.connection.execute(
            "SELECT status FROM generation_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        snapshot = self.connection.execute(
            "SELECT status FROM live_source_snapshots WHERE id = ?", (snapshot_id,)
        ).fetchone()
        if job is None or job[0] != "prepared" or snapshot is None or snapshot[0] not in {"verified", "changed"}:
            raise GenerationError("invalid_live_evidence", "Evidência ao vivo não pode ser vinculada.")
        rank = self.connection.execute(
            "SELECT coalesce(max(rank), 0) + 1 FROM generation_evidence WHERE job_id = ?",
            (job_id,),
        ).fetchone()[0]
        self.connection.execute(
            "INSERT INTO generation_evidence(job_id, evidence_kind, live_snapshot_id, rank) "
            "VALUES (?, 'live', ?, ?)",
            (job_id, snapshot_id, rank),
        )

    def commit(self, job_id: str, payload: dict[str, Any]) -> PublicQuestion:
        draft = _parse_draft(payload)
        request_hash = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        receipt_id = f"generation:{job_id}"
        receipt = self.connection.execute(
            "SELECT request_hash, response_json FROM operation_receipts WHERE id = ?",
            (receipt_id,),
        ).fetchone()
        if receipt:
            if receipt[0] != request_hash:
                raise GenerationError("idempotency_conflict", "A repetição diverge do congelamento original.")
            stored = json.loads(receipt[1])
            return PublicQuestion(
                id=stored["id"], discipline=Discipline(stored["discipline"]),
                topic_id=stored["topic_id"], statement=stored["statement"],
                origin_label=stored["origin_label"],
            )
        job = self.connection.execute(
            """
            SELECT session_id, discipline, topic_id, concept_key, difficulty, status, update_policy
            FROM generation_jobs WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
        if job is None or job[5] != "prepared":
            raise GenerationError("job_not_prepared", "Trabalho de geração não está preparado.")
        self._open_session(job[0])
        if job[6] == "selective_live_confirmation" and not self.connection.execute(
            "SELECT 1 FROM generation_evidence WHERE job_id = ? AND evidence_kind = 'live' LIMIT 1",
            (job_id,),
        ).fetchone():
            raise GenerationError(
                "live_confirmation_required",
                "Este trabalho exige evidência institucional ao vivo antes do congelamento.",
            )
        if draft.difficulty != job[4]:
            raise GenerationError("scope_drift", "A dificuldade diverge do trabalho preparado.")
        content_hash = hashlib.sha256(draft.statement.encode("utf-8")).hexdigest()
        if self.connection.execute(
            "SELECT 1 FROM questions WHERE content_hash = ? OR trim(statement) = ? LIMIT 1",
            (content_hash, draft.statement),
        ).fetchone():
            raise GenerationError("literal_repetition", "A questão é repetição literal de item já aplicado ou exemplar.")
        candidate_terms = set(re.findall(r"[a-z0-9áàâãéêíóôõúç]+", draft.statement.casefold()))
        for (prior_statement,) in self.connection.execute(
            """
            SELECT statement FROM questions
            WHERE concept_key = ?
            UNION
            SELECT question.statement
            FROM approved_exemplars AS exemplar
            JOIN questions AS question ON question.id = exemplar.question_id
            WHERE exemplar.state = 'approved' AND question.concept_key = ?
            """,
            (job[3], job[3]),
        ).fetchall():
            prior_terms = set(re.findall(r"[a-z0-9áàâãéêíóôõúç]+", prior_statement.casefold()))
            similarity = len(candidate_terms & prior_terms) / max(len(candidate_terms | prior_terms), 1)
            if similarity >= 0.82:
                raise GenerationError(
                    "near_repetition",
                    "A questão é repetição material muito próxima de item já aplicado ou exemplar.",
                )
        prepared_ids = {
            row[0]
            for row in self.connection.execute(
                """
                SELECT coalesce(source_chunk_id, live_snapshot_id)
                FROM generation_evidence WHERE job_id = ?
                """,
                (job_id,),
            )
        }
        foreign = [link["evidence_id"] for link in draft.evidence_links if link["evidence_id"] not in prepared_ids]
        if foreign:
            # Lista os identificadores aceitos para o host corrigir o vínculo sem tentativa às cegas.
            raise GenerationError(
                "foreign_evidence",
                "A questão cita evidência não preparada para este trabalho: "
                f"{', '.join(foreign)}. Use em evidence_id exatamente o campo \"id\" de um item de "
                f"generation_brief.evidence ou de pmal_search_evidence deste trabalho: {', '.join(sorted(prepared_ids))}.",
            )
        evidence_by_id = {item.id: item for item in self._evidence_for_job(job_id)}
        if any(link["evidence_id"] not in evidence_by_id for link in draft.evidence_links):
            raise GenerationError("invalid_evidence", "A evidência foi invalidada ou não está mais disponível.")
        question_id = str(uuid.uuid4())
        now = self._now().isoformat()
        with transaction(self.connection):
            self.connection.execute(
                """
                INSERT INTO questions(
                    id, origin, discipline, topic_id, statement, answer, rationale,
                    status, relevance, validated_at, generation_job_id,
                    delivery_policy, difficulty, concept_key, content_hash,
                    construction_pattern, decisive_expression, trap, distinction
                ) VALUES (?, 'original', ?, ?, ?, ?, ?, 'validated', 'direct', ?, ?,
                          'session_only', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    question_id, job[1], job[2], draft.statement, draft.answer,
                    draft.rationale, now, job_id, draft.difficulty, job[3], content_hash,
                    draft.construction_pattern, draft.decisive_expression,
                    draft.trap, draft.distinction,
                ),
            )
            for link in draft.evidence_links:
                evidence = evidence_by_id[link["evidence_id"]]
                self.connection.execute(
                    """
                    INSERT INTO question_evidence(
                        question_id, claim_key, evidence_kind, source_chunk_id,
                        live_snapshot_id, locator, excerpt, evidence_hash, authority
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        question_id, link["claim_key"], evidence.kind,
                        evidence.id if evidence.kind == "local" else None,
                        evidence.id if evidence.kind == "live" else None,
                        evidence.locator, evidence.excerpt, evidence.sha256, evidence.authority,
                    ),
                )
                if evidence.kind == "local":
                    source = self.connection.execute(
                        "SELECT document_id, page_start FROM source_chunks WHERE id = ?",
                        (evidence.id,),
                    ).fetchone()
                    self.connection.execute(
                        "INSERT OR IGNORE INTO question_sources(question_id, document_id, page_number, source_role) "
                        "VALUES (?, ?, ?, 'rationale')",
                        (question_id, source[0], source[1]),
                    )
            self.connection.execute(
                "INSERT INTO session_questions(session_id, question_id, generation_job_id, assigned_at) "
                "VALUES (?, ?, ?, ?)",
                (job[0], question_id, job_id, now),
            )
            self.connection.execute(
                "UPDATE generation_jobs SET status = 'committed', committed_at = ?, question_id = ? WHERE id = ?",
                (now, question_id, job_id),
            )
            response = {
                "id": question_id, "discipline": job[1], "topic_id": job[2],
                "statement": draft.statement, "origin_label": "inédita",
            }
            self.connection.execute(
                "INSERT INTO operation_receipts(id, operation_kind, request_hash, response_json, created_at) "
                "VALUES (?, 'commit_generated_question', ?, ?, ?)",
                (receipt_id, request_hash, json.dumps(response, ensure_ascii=False, separators=(",", ":")), now),
            )
        return PublicQuestion(
            id=question_id, discipline=Discipline(job[1]), topic_id=job[2],
            statement=draft.statement, origin_label="inédita",
        )
