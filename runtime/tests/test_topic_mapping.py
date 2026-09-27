"""Classificação estrutural de disciplina, autoridade e tópico do acervo."""

import sqlite3

from pmal_study.evidence import search_evidence
from pmal_study.sources import _infer_authority, _infer_discipline, enrich_chunk_metadata
from pmal_study.topic_mapping import first_article, law_topic, topic_for_article


def test_faixa_de_artigos_escolhe_o_topico_mais_especifico():
    assert topic_for_article("direito_processual_penal_militar", 499) == (True, "dppm.18")
    assert topic_for_article("direito_processual_penal_militar", 244) == (True, "dppm.14.1")
    assert topic_for_article("direito_processual_penal_militar", 230) == (True, "dppm.14")
    assert topic_for_article("direito_penal_militar", 9) == (True, "dpm.15")
    assert topic_for_article("direito_penal_militar", 400) == (True, None)
    assert topic_for_article("direito_penal_militar", 999) == (False, None)


def test_detecta_artigo_e_numero_da_lei():
    assert first_article("TÍTULO I\nArt. 499. Nenhum ato judicial") == 499
    assert first_article("Art 498. O Superior Tribunal Militar") == 498
    assert first_article("§ 1º É de cinco dias o prazo") is None
    assert law_topic("Legislação Oficial/Lei 8.072-1990 - Crimes Hediondos.pdf") == "leg.4"
    assert law_topic("Legislação Oficial/Decreto 37.042-1996 RDPMAL.pdf") == "leg.2"
    assert law_topic("Legislação Oficial/Lei 9.381-2024 - Altera Estatuto PMAL.pdf") == "leg.1"
    assert law_topic("CPM/2024-05-21-imputabilidade.pdf") is None


def test_apostila_nao_e_copia_oficial():
    assert _infer_authority("Legislação PMAL/Estatuto - Parte 1.pdf") == "didactic"
    assert _infer_authority("Legislação PMAL/RDPMAL - Parte 2.pdf") == "didactic"
    assert _infer_authority("CPPM - DEL1002Compilado.pdf") == "local_official_copy"
    assert _infer_authority("Legislação Oficial/Lei 5.346-1992 Estatuto.pdf") == "local_official_copy"
    assert _infer_authority("EDITAL PMAL 2026.pdf") == "exam_board"
    assert _infer_discipline("Legislação Oficial/Lei 11.343-2006.pdf") == "legislacao_pmal"
    assert _infer_discipline("BIZU ESTRATÉGIA/BIZU - INFORMÁTICA.pdf") is None


def _seed_chunk(connection: sqlite3.Connection, relative_path: str, texts: list[str]) -> list[str]:
    import hashlib

    document_id = hashlib.sha256(relative_path.encode()).hexdigest()
    version_id = f"v-{document_id[:16]}"
    connection.execute(
        "INSERT INTO source_documents(id, relative_path, document_type, sha256, page_count, status) "
        "VALUES (?, ?, 'pdf', ?, 1, 'usable')",
        (document_id, relative_path, document_id),
    )
    connection.execute(
        "INSERT INTO source_document_versions(id, document_id, sha256, page_count, status, processed_at, is_current) "
        "VALUES (?, ?, ?, 1, 'usable', '2026-09-27T00:00:00+00:00', 1)",
        (version_id, document_id, document_id),
    )
    ids = []
    for index, text in enumerate(texts, start=1):
        chunk_id = hashlib.sha256(f"{relative_path}:{index}".encode()).hexdigest()
        connection.execute(
            "INSERT INTO source_chunks(id, version_id, document_id, page_start, page_end, locator, text, sha256, "
            "discipline, source_kind, authority, status, created_at) "
            "VALUES (?, ?, ?, 1, 1, ?, ?, ?, NULL, 'pdf', 'didactic', 'usable', '2026-09-27T00:00:00+00:00')",
            (chunk_id, version_id, document_id, f"p. 1, bloco {index}", text, chunk_id),
        )
        ids.append(chunk_id)
    return ids


def test_enriquecimento_estrutural_e_busca_restrita_a_disciplina(db_connection):
    compiled = _seed_chunk(db_connection, "Teste/CPPM - DEL1002Compilado.pdf", [
        "DAS NULIDADES Art. 499. Nenhum ato judicial será declarado nulo se da nulidade não resultar prejuízo.",
        "Parágrafo único. Continuação do regime das nulidades processuais militares.",
    ])
    outside = _seed_chunk(db_connection, "Teste/BIZU - INFORMÁTICA.pdf", [
        "Nulidades e prejuízo processual são irrelevantes para planilhas eletrônicas e nulidade de células.",
    ])
    enrich_chunk_metadata(db_connection)
    rows = dict(db_connection.execute(
        "SELECT id, topic_id FROM source_chunks WHERE id IN (?, ?, ?)", (*compiled, *outside)
    ).fetchall())
    assert rows[compiled[0]] == "dppm.18"
    assert rows[compiled[1]] == "dppm.18"
    assert db_connection.execute(
        "SELECT authority, discipline FROM source_chunks WHERE id = ?", (compiled[0],)
    ).fetchone() == ("local_official_copy", "direito_processual_penal_militar")
    assert db_connection.execute(
        "SELECT discipline FROM source_chunks WHERE id = ?", (outside[0],)
    ).fetchone() == (None,)

    evidence = search_evidence(
        db_connection, discipline="direito_processual_penal_militar",
        topic_id="dppm.18", query="nulidade prejuízo", limit=8,
    )
    identifiers = {item.id for item in evidence}
    assert compiled[0] in identifiers
    assert outside[0] not in identifiers
