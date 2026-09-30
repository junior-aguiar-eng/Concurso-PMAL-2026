#!/usr/bin/env python3
"""Empacota o plugin do ChatGPT (plugin.json + skills/) em dist/treinador-pmal-chatgpt-<versão>.zip.

O motor (runtime/src), o edital (runtime/config) e as questões revisadas são copiados do
repositório. O acervo de leis vem de uma pasta de PDFs oficiais e/ou do banco-semente pessoal:

  python clients/chatgpt/build_plugin.py --leis "C:/Users/.../Desktop/PMAL"
  python clients/chatgpt/build_plugin.py --seed C:/caminho/pmal-study-seed.db

--leis indexa cada PDF da pasta (sem subpastas) como cópia oficial de norma; requer o pacote
pypdf, instalado automaticamente se ausente. O tópico do edital decorre do número da lei no
nome do arquivo (ex.: L9099.pdf); CPM e CPPM devem conter DEL1001/DEL1002 no nome.

O banco-semente passa por scripts/build_public_seed.py: mantém o texto integral das normas
oficiais, do edital e das provas e remove o texto de material didático de terceiros.
Use --manter-didatico para embutir o banco completo (uso estritamente pessoal).
"""

from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
import os
import shutil
import sqlite3
from contextlib import closing
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SKILL = "treinador-pmal-chatgpt"
LIMIT_BYTES = 100 * 1024 * 1024
OFFICIAL = ("local_official_copy", "official_legislation", "exam_board")


def _public_seed(source: Path, target: Path) -> dict[str, int]:
    spec = importlib.util.spec_from_file_location("build_public_seed", REPO / "scripts" / "build_public_seed.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    # Banco de uso (com histórico) referencia trechos didáticos em evidências de questões e
    # gerações; build_public_seed apaga esses trechos com chaves estrangeiras ativas.
    # Remove antes, numa cópia, as referências aos trechos que serão apagados.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        working = Path(temporary) / "seed.db"
        shutil.copyfile(source, working)
        with closing(sqlite3.connect(working)) as connection:
            documents = connection.execute("SELECT id, relative_path FROM source_documents").fetchall()
            private = [
                document_id for document_id, relative_path in documents
                if not module.is_public(relative_path, {
                    row[0] for row in connection.execute(
                        "SELECT DISTINCT authority FROM source_chunks WHERE document_id = ?", (document_id,)
                    )
                })
            ]
            with connection:
                for document_id in private:
                    for table in ("generation_evidence", "question_evidence", "exam_item_sources"):
                        connection.execute(
                            f"DELETE FROM {table} WHERE source_chunk_id IN "
                            "(SELECT id FROM source_chunks WHERE document_id = ?)",
                            (document_id,),
                        )
        return module.build(working, target)


# Nomes sem número de lei que o mapeamento de tópicos não reconhece.
_ALIASES = (
    (("rdpmal",), "D37042"),
    (("regulamento", "disciplinar"), "D37042"),
    (("estatuto", "pmal"), "L5346"),
    (("estatuto", "policiais", "militares"), "L5346"),
)


def _staged_name(filename: str) -> str:
    sys.path.insert(0, str(REPO / "runtime" / "src"))
    from pmal_study.topic_mapping import compiled_code_discipline, law_topic, normalize  # noqa: PLC0415

    if compiled_code_discipline(filename) or law_topic(filename):
        return filename
    folded = normalize(filename)
    for words, prefix in _ALIASES:
        if all(word in folded for word in words):
            return f"{prefix} {filename}"
    return filename


def _pdf_reader():
    try:
        from pypdf import PdfReader  # noqa: PLC0415
    except ImportError:
        print("Instalando pypdf (leitura de PDF)...")
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "pypdf"], check=True)
        from pypdf import PdfReader  # noqa: PLC0415
    return PdfReader


def _index_laws(folder: Path, database: Path) -> dict[str, object]:
    """Indexa PDFs de normas com o próprio núcleo, trocando poppler/tesseract por pypdf."""

    PdfReader = _pdf_reader()
    sys.path.insert(0, str(REPO / "runtime" / "src"))
    from pmal_study import sources  # noqa: PLC0415
    from pmal_study.bootstrap import bootstrap_bundled_runtime  # noqa: PLC0415
    from pmal_study.db import open_database  # noqa: PLC0415
    from pmal_study.topic_mapping import compiled_code_discipline, law_topic  # noqa: PLC0415

    def page_count(path: Path) -> tuple[int, str | None]:
        try:
            return len(PdfReader(str(path)).pages), None
        except Exception as error:  # noqa: BLE001 - PDF corrompido vira diagnóstico
            return 0, f"pypdf: {error}"

    def all_pages(path: Path, count: int) -> list[str]:
        try:
            pages = [sources._normalize_text(page.extract_text() or "") for page in PdfReader(str(path)).pages]
        except Exception:  # noqa: BLE001
            pages = []
        return (pages + [""] * count)[:count]

    sources._read_page_count = page_count
    sources._extract_all_text_pages = all_pages
    sources._ocr_page = lambda *args, **kwargs: ""

    pdfs = sorted(path for path in folder.iterdir() if path.is_file() and path.suffix.casefold() == ".pdf")
    if not pdfs:
        raise SystemExit(f"Nenhum PDF encontrado em {folder}")
    unmapped = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        root = Path(temporary)
        official = root / "Legislacao Oficial"
        official.mkdir()
        for pdf in pdfs:
            name = _staged_name(pdf.name)
            if not (compiled_code_discipline(name) or law_topic(name)):
                unmapped.append(pdf.name)
            shutil.copyfile(pdf, official / name)
        connection = open_database(database)
        try:
            bootstrap_bundled_runtime(REPO / "runtime", connection)
            report = sources.refresh_corpus(root, connection)
        finally:
            connection.close()
    print(f"PDFs indexados: {len(pdfs)}")
    if unmapped:
        print("AVISO: sem tópico do edital pelo nome (renomeie com o número da lei):", ", ".join(unmapped))
    return {"relatorio": getattr(report, "__dict__", str(report))}


def _seed_report(path: Path) -> dict[str, object]:
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as connection:
        marks = ",".join("?" for _ in OFFICIAL)
        official = connection.execute(
            f"SELECT count(*) FROM source_chunks WHERE authority IN ({marks})", OFFICIAL
        ).fetchone()[0]
        empty = [
            row[0] for row in connection.execute(
                f"""
                SELECT topic.id FROM syllabus_topics AS topic
                WHERE topic.parent_id IS NOT NULL AND topic.depth = 1 AND NOT EXISTS (
                    SELECT 1 FROM source_chunks AS chunk
                    WHERE chunk.topic_id = topic.id AND chunk.authority IN ({marks}))
                ORDER BY topic.id
                """,
                OFFICIAL,
            )
        ]
        attempts = connection.execute("SELECT count(*) FROM attempt_events").fetchone()[0]
    return {"trechos_normativos": official, "tentativas_embutidas": attempts, "topicos_sem_norma": empty}


TEXT_SUFFIXES = {".md", ".json", ".jsonl", ".yaml", ".yml", ".py", ".txt", ".toml"}


def _normalized(path: Path) -> bytes:
    """Converte CRLF em LF: o Git no Windows pode gravar os textos com CRLF."""

    data = path.read_bytes()
    return data.replace(b"\r\n", b"\n") if path.suffix.casefold() in TEXT_SUFFIXES else data


def _files(root: Path):
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise SystemExit(f"Link simbólico não suportado: {path}")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            yield path


def _split_seed(database: Path, part_mb: float) -> list[Path]:
    """Compacta o banco e o divide em partes pequenas; scripts/pmal.py remonta no init.

    O ChatGPT recusa pacotes com arquivos grandes dentro da skill, mesmo abaixo de 100 MB.
    As partes usam a extensão .db, já aceita pelo uploader.
    """

    with closing(sqlite3.connect(database)) as connection:
        connection.execute("VACUUM")
    payload = gzip.compress(database.read_bytes(), compresslevel=9)
    database.unlink()
    folder = database.parent / "acervo"
    folder.mkdir()
    size = int(part_mb * 1024 * 1024)
    parts = []
    for index in range(0, len(payload), size):
        part = folder / f"acervo-{index // size + 1:03d}.db"
        part.write_bytes(payload[index:index + size])
        parts.append(part)
    return parts


def build(seed: Path | None, laws: Path | None, keep_didactic: bool, output: Path, part_mb: float = 4) -> Path:
    manifest = json.loads((HERE / "plugin.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        stage = Path(temporary) / "stage"
        skill = stage / "skills" / SKILL
        shutil.copytree(HERE / "skills" / SKILL, skill)
        runtime = skill / "runtime"
        shutil.copytree(REPO / "runtime" / "src", runtime / "src",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(REPO / "runtime" / "config", runtime / "config")
        shutil.copytree(REPO / "runtime" / "corpus" / "questions", runtime / "corpus" / "questions")
        shutil.copyfile(HERE / "plugin.json", stage / "plugin.json")

        target = runtime / "corpus" / "pmal-study-seed.db"
        if seed:
            if keep_didactic:
                shutil.copyfile(seed, target)
                print("Banco-semente completo embutido (inclui material didático).")
            else:
                print("Banco-semente filtrado:", _public_seed(seed, target))
        if laws:
            _index_laws(laws, target)
        if target.exists():
            print("Cobertura normativa:", json.dumps(_seed_report(target), ensure_ascii=False))
            parts = _split_seed(target, part_mb)
            print(f"Acervo compactado em {len(parts)} partes de até {part_mb} MB.")
        else:
            print("AVISO: sem banco-semente nem pasta de leis. O plugin terá só as questões revisadas; "
                  "não haverá leis nem geração de questões inéditas.")

        output.mkdir(parents=True, exist_ok=True)
        archive = output / f"{SKILL}-{version}.zip"
        with ZipFile(archive, "w", ZIP_DEFLATED) as bundle:
            for path in _files(stage):
                info = ZipInfo(path.relative_to(stage).as_posix(), date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                bundle.writestr(info, _normalized(path))
    size = archive.stat().st_size
    if size > LIMIT_BYTES:
        archive.unlink()
        raise SystemExit(f"Pacote com {size / 2**20:.1f} MB excede o limite de 100 MB do ChatGPT.")
    print(f"Pacote gerado: {archive} ({size / 2**20:.1f} MB)")
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=Path, default=os.environ.get("PMAL_SEED_DB") or None,
                        help="banco-semente pessoal (padrão: PMAL_SEED_DB ou runtime/corpus/pmal-study-seed.db)")
    parser.add_argument("--leis", type=Path, help="pasta com os PDFs oficiais das normas")
    parser.add_argument("--manter-didatico", action="store_true",
                        help="embute o banco completo, sem remover material de terceiros")
    parser.add_argument("--parte-mb", type=float, default=4, help="tamanho máximo de cada parte do acervo (MB)")
    parser.add_argument("--output", type=Path, default=HERE / "dist")
    args = parser.parse_args()
    seed = args.seed
    if seed is None and (REPO / "runtime" / "corpus" / "pmal-study-seed.db").is_file():
        seed = REPO / "runtime" / "corpus" / "pmal-study-seed.db"
    if seed is not None and not Path(seed).is_file():
        print(f"Banco-semente não encontrado: {seed}", file=sys.stderr)
        return 1
    if args.leis is not None and not args.leis.is_dir():
        print(f"Pasta de leis não encontrada: {args.leis}", file=sys.stderr)
        return 1
    build(Path(seed) if seed else None, args.leis, args.manter_didatico, args.output, args.parte_mb)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
