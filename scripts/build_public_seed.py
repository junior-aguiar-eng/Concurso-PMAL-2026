"""Gera um banco-semente compartilhável a partir do banco-semente pessoal.

Mantém o texto integral apenas de fontes públicas: cópias oficiais de normas
(autoridade local_official_copy), o edital e os cadernos de prova. Material didático
de terceiros (apostilas, resumos, cursos) perde todo o texto: trechos e páginas são
apagados, restando só a referência (arquivo e página) exigida pelas questões que a
citam. O hash desses documentos é trocado por um marcador, para que quem possuir o
PDF original o reindexe normalmente em "Atualizar acervo".

Uso: python scripts/build_public_seed.py <seed-pessoal.db> <seed-publico.db>
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import sys
import unicodedata
from pathlib import Path

PUBLIC_EXAMS = ("cespe", "cebraspe", "_matriz", "edital")


def _normalize(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value.casefold())
    return "".join(character for character in folded if not unicodedata.combining(character))


def is_public(relative_path: str, authorities: set[str]) -> bool:
    if "local_official_copy" in authorities or "exam_board" in authorities:
        return True
    filename = _normalize(relative_path).rsplit("/", 1)[-1]
    return any(marker in filename for marker in PUBLIC_EXAMS)


def build(source: Path, target: Path) -> dict[str, int]:
    shutil.copyfile(source, target)
    connection = sqlite3.connect(target)
    connection.execute("PRAGMA foreign_keys = ON")
    stats = {"public": 0, "stripped": 0, "removed": 0, "reference_pages": 0}
    documents = connection.execute("SELECT id, relative_path FROM source_documents").fetchall()
    with connection:
        for document_id, relative_path in documents:
            authorities = {
                row[0] for row in connection.execute(
                    "SELECT DISTINCT authority FROM source_chunks WHERE document_id = ?", (document_id,)
                )
            }
            if is_public(relative_path, authorities):
                stats["public"] += 1
                continue
            connection.execute("DELETE FROM source_chunks WHERE document_id = ?", (document_id,))
            referenced = {
                row[0] for row in connection.execute(
                    "SELECT DISTINCT page_number FROM question_sources WHERE document_id = ?", (document_id,)
                )
            }
            connection.execute(
                f"DELETE FROM source_pages WHERE document_id = ? AND page_number NOT IN "
                f"({','.join('?' * len(referenced)) or 'NULL'})",
                (document_id, *sorted(referenced)),
            )
            if not referenced:
                connection.execute("DELETE FROM source_document_versions WHERE document_id = ?", (document_id,))
                connection.execute("DELETE FROM source_documents WHERE id = ?", (document_id,))
                stats["removed"] += 1
                continue
            connection.execute(
                """
                UPDATE source_pages
                SET text = '', status = 'usable', page_state = 'bundled_reference',
                    extraction_method = 'bundled', content_hash = NULL,
                    letter_ratio = NULL, replacement_ratio = NULL, quarantine_reason = NULL
                WHERE document_id = ?
                """,
                (document_id,),
            )
            connection.execute(
                """
                UPDATE source_documents
                SET sha256 = ?, status = 'usable', quality = NULL,
                    extraction_diagnostics = 'Material de terceiros: apenas referência de página. '
                        || 'Coloque o PDF na pasta do acervo e use Atualizar acervo para indexá-lo.'
                WHERE id = ?
                """,
                (hashlib.sha256(f"public-seed:{relative_path}".encode()).hexdigest(), document_id),
            )
            stats["stripped"] += 1
            stats["reference_pages"] += len(referenced)
    for table in ("source_chunks_fts", "source_pages_fts"):
        connection.execute(f"INSERT INTO {table}({table}) VALUES ('rebuild')")
    connection.commit()
    connection.execute("VACUUM")
    connection.close()
    return stats


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    print(build(Path(sys.argv[1]), Path(sys.argv[2])))
