"""Importação estrita do recorte canônico do edital de Oficial."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pmal_study.db import transaction
from pmal_study.models import Discipline


@dataclass(frozen=True, slots=True)
class SyllabusTopic:
    id: str
    discipline: Discipline
    parent_id: str | None
    title: str
    order_index: int
    depth: int
    source_document: str
    source_page: int


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Campo textual inválido: {field}")
    return value.strip()


def _validate_sibling_order(topics: list[dict[str, Any]], parent_id: str) -> None:
    numbers: list[int] = []
    for topic in topics:
        number = _require_text(topic.get("number"), f"{parent_id}.number")
        try:
            numbers.append(int(number.rsplit(".", maxsplit=1)[-1]))
        except ValueError as error:
            raise ValueError(f"Numeração inválida: {number}") from error
    expected = list(range(1, len(numbers) + 1))
    if numbers != expected:
        raise ValueError(
            f"Ordem não contígua sob {parent_id}: esperado {expected}, recebido {numbers}"
        )


def _flatten(payload: dict[str, Any]) -> list[SyllabusTopic]:
    if payload.get("version") != 1:
        raise ValueError("Versão do recorte não suportada.")
    source_document = _require_text(payload.get("source_document"), "source_document")
    disciplines = payload.get("disciplines")
    if not isinstance(disciplines, list) or not disciplines:
        raise ValueError("O recorte deve conter disciplinas.")

    flattened: list[SyllabusTopic] = []
    identifiers: set[str] = set()
    for root_order, raw_discipline in enumerate(disciplines, start=1):
        if not isinstance(raw_discipline, dict):
            raise ValueError("Registro de disciplina inválido.")
        raw_value = _require_text(raw_discipline.get("discipline"), "discipline")
        try:
            discipline = Discipline(raw_value)
        except ValueError as error:
            raise ValueError(f"Disciplina fora do escopo: {raw_value}") from error
        root_id = _require_text(raw_discipline.get("id"), "id")
        if root_id in identifiers:
            raise ValueError(f"ID duplicado: {root_id}")
        identifiers.add(root_id)
        source_page = raw_discipline.get("source_page")
        if not isinstance(source_page, int) or source_page <= 0:
            raise ValueError(f"Página de origem inválida para {root_id}.")
        flattened.append(
            SyllabusTopic(
                id=root_id,
                discipline=discipline,
                parent_id=None,
                title=_require_text(raw_discipline.get("title"), f"{root_id}.title"),
                order_index=root_order,
                depth=0,
                source_document=source_document,
                source_page=source_page,
            )
        )

        topics = raw_discipline.get("topics")
        if not isinstance(topics, list) or not topics:
            raise ValueError(f"Disciplina sem tópicos: {root_id}")
        _validate_sibling_order(topics, root_id)
        for raw_topic in topics:
            number = _require_text(raw_topic.get("number"), f"{root_id}.number")
            if "." in number:
                raise ValueError(f"Tópico principal inválido: {number}")
            topic_id = f"{root_id}.{number}"
            if topic_id in identifiers:
                raise ValueError(f"ID duplicado: {topic_id}")
            identifiers.add(topic_id)
            flattened.append(
                SyllabusTopic(
                    id=topic_id,
                    discipline=discipline,
                    parent_id=root_id,
                    title=_require_text(raw_topic.get("title"), f"{topic_id}.title"),
                    order_index=int(number),
                    depth=1,
                    source_document=source_document,
                    source_page=source_page,
                )
            )

            children = raw_topic.get("children", [])
            if not isinstance(children, list):
                raise ValueError(f"Subtópicos inválidos: {topic_id}")
            if not children:
                continue
            _validate_sibling_order(children, topic_id)
            for raw_child in children:
                child_number = _require_text(
                    raw_child.get("number"), f"{topic_id}.number"
                )
                if not child_number.startswith(f"{number}."):
                    raise ValueError(
                        f"Subtópico {child_number} não pertence a {number}."
                    )
                child_id = f"{root_id}.{child_number}"
                if child_id in identifiers:
                    raise ValueError(f"ID duplicado: {child_id}")
                identifiers.add(child_id)
                flattened.append(
                    SyllabusTopic(
                        id=child_id,
                        discipline=discipline,
                        parent_id=topic_id,
                        title=_require_text(
                            raw_child.get("title"), f"{child_id}.title"
                        ),
                        order_index=int(child_number.rsplit(".", maxsplit=1)[-1]),
                        depth=2,
                        source_document=source_document,
                        source_page=source_page,
                    )
                )
    return flattened


def import_syllabus(path: Path, connection: sqlite3.Connection) -> int:
    """Valida e substitui atomicamente o recorte canônico do edital."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("O arquivo do edital deve conter um objeto JSON.")
    topics = _flatten(payload)

    with transaction(connection):
        connection.execute("DELETE FROM syllabus_topics")
        connection.executemany(
            """
            INSERT INTO syllabus_topics(
                id, discipline, parent_id, title, order_index, depth,
                source_document, source_page
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    topic.id,
                    topic.discipline.value,
                    topic.parent_id,
                    topic.title,
                    topic.order_index,
                    topic.depth,
                    topic.source_document,
                    topic.source_page,
                )
                for topic in topics
            ),
        )
    return len(topics)


def list_syllabus(connection: sqlite3.Connection) -> list[SyllabusTopic]:
    """Lista o recorte na ordem hierárquica original do edital."""

    rows = connection.execute(
        """
        WITH RECURSIVE ordered_topics(
            id, discipline, parent_id, title, order_index, depth,
            source_document, source_page, sort_path
        ) AS (
            SELECT id, discipline, parent_id, title, order_index, depth,
                   source_document, source_page, printf('%03d', order_index)
            FROM syllabus_topics
            WHERE parent_id IS NULL
            UNION ALL
            SELECT child.id, child.discipline, child.parent_id, child.title,
                   child.order_index, child.depth, child.source_document,
                   child.source_page,
                   parent.sort_path || '.' || printf('%03d', child.order_index)
            FROM syllabus_topics AS child
            JOIN ordered_topics AS parent ON child.parent_id = parent.id
        )
        SELECT id, discipline, parent_id, title, order_index, depth,
               source_document, source_page
        FROM ordered_topics
        ORDER BY sort_path
        """
    ).fetchall()
    return [
        SyllabusTopic(
            id=row[0],
            discipline=Discipline(row[1]),
            parent_id=row[2],
            title=row[3],
            order_index=row[4],
            depth=row[5],
            source_document=row[6],
            source_page=row[7],
        )
        for row in rows
    ]
