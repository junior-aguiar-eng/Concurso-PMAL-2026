"""Relatórios derivados do estado local, sem duplicar a fonte de verdade."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path


_LABELS = {
    "direito_penal_militar": "Direito Penal Militar",
    "direito_processual_penal_militar": "Direito Processual Penal Militar",
    "legislacao_pmal": "Legislação PMAL",
    "conhecimentos_alagoas": "Conhecimentos de Alagoas",
}


def export_canvas_summary(
    connection: sqlite3.Connection, generated_at: datetime
) -> str:
    """Produz Markdown determinístico, sintético e seguro para leitura no Canvas."""

    if generated_at.tzinfo is None:
        raise ValueError("generated_at deve conter fuso horário")
    from pmal_study.sessions import StudyService

    service = StudyService(connection, clock=lambda: generated_at)
    dashboard = service.get_dashboard()
    review_queue = service.get_review_queue()
    metrics = list(dashboard["discipline_metrics"])
    weakest = sorted(
        metrics,
        key=lambda item: (float(item["mastery"]), int(item["attempts"]), str(item["discipline"])),
    )
    due = [item for item in review_queue if item["due"]]
    coverage = dashboard["syllabus"]
    corpus = dashboard["corpus"]
    applied = dashboard["questions_applied"]
    exemplars = dashboard["exemplars"]
    next_session = (
        f"Revisão dirigida das {len(due)} questões vencidas."
        if due
        else f"Treino por disciplina: {_LABELS[str(weakest[0]['discipline'])]}."
    )
    weak_lines = "\n".join(
        f"- {_LABELS[str(item['discipline'])]}: "
        f"{float(item['mastery']) * 100:.1f}% de domínio, "
        f"{int(item['attempts'])} respostas."
        for item in weakest
    )
    coverage_lines = "\n".join(
        f"- {_LABELS[str(item['discipline'])]}: "
        f"{int(item['topics_covered'])}/{int(item['topics_total'])} tópicos."
        for item in metrics
    )
    due_lines = (
        "\n".join(
            f"- {item['topic_id']} — {_LABELS[str(item['discipline'])]} — "
            f"prevista para {item['next_review_at']}."
            for item in due[:20]
        )
        if due
        else "Nenhuma revisão vencida."
    )
    return (
        "# PMAL Oficial — painel de estudos\n\n"
        f"Gerado em: `{generated_at.isoformat()}`.\n\n"
        "## Situação\n\n"
        f"- Questões respondidas: {int(dashboard['attempts_total'])}\n"
        f"- Aproveitamento: {float(dashboard['accuracy']) * 100:.1f}%\n"
        f"- Questões validadas no banco: {int(dashboard['study_bank'])}\n\n"
        "## Revisões vencidas\n\n"
        f"{due_lines}\n\n"
        "## Pontos fracos\n\n"
        f"{weak_lines}\n\n"
        "## Cobertura do edital\n\n"
        f"Total: {int(coverage['topics_covered'])}/{int(coverage['topics_total'])} tópicos.\n\n"
        f"{coverage_lines}\n\n"
        "## Acervo e evidências\n\n"
        f"- Documentos: {int(corpus['documents'])}\n"
        f"- Páginas rastreadas: {int(corpus['total_pages'])}\n"
        f"- Páginas por OCR: {int(corpus['ocr_pages'])}\n"
        f"- Páginas em quarentena: {int(corpus['quarantined_pages'])}\n"
        f"- Fontes desatualizadas ou inválidas: {int(corpus['stale_sources'])}\n\n"
        "## Questões aplicadas e exemplares\n\n"
        f"- Oficiais aplicadas: {int(applied['official'])}\n"
        f"- Inéditas aplicadas: {int(applied['generated'])}\n"
        f"- Exemplares aprovados: {int(exemplars['approved'])}\n\n"
        "## Próxima sessão\n\n"
        f"{next_session}\n\n"
        "> Este Markdown é uma visualização derivada. O SQLite local permanece a fonte autoritativa; edições no Canvas não alteram o histórico de estudo.\n"
    )


def write_canvas_summary(
    connection: sqlite3.Connection,
    generated_at: datetime,
    destination: Path,
) -> str:
    """Grava o relatório por substituição atômica e devolve o conteúdo."""

    markdown = export_canvas_summary(connection, generated_at)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{destination.stem}-",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as temporary:
            temporary.write(markdown)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return markdown
