#!/usr/bin/env python3
"""Empacota o plugin do ChatGPT (plugin.json + skills/) em dist/treinador-pmal-chatgpt-<versão>.zip.

O motor (runtime/src), o edital (runtime/config) e as questões revisadas são copiados do
repositório. O acervo de leis vem do banco-semente pessoal, que não é versionado:

  python clients/chatgpt/build_plugin.py --seed C:/caminho/pmal-study-seed.db

Por padrão, o banco passa por scripts/build_public_seed.py: mantém o texto integral das
normas oficiais, do edital e das provas e remove o texto de material didático de terceiros.
Use --manter-didatico para embutir o banco completo (uso estritamente pessoal).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sqlite3
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
    return module.build(source, target)


def _seed_report(path: Path) -> dict[str, object]:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
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


def _files(root: Path):
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise SystemExit(f"Link simbólico não suportado: {path}")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            yield path


def build(seed: Path | None, keep_didactic: bool, output: Path) -> Path:
    manifest = json.loads((HERE / "plugin.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    with tempfile.TemporaryDirectory() as temporary:
        stage = Path(temporary) / "stage"
        skill = stage / "skills" / SKILL
        shutil.copytree(HERE / "skills" / SKILL, skill)
        runtime = skill / "runtime"
        shutil.copytree(REPO / "runtime" / "src", runtime / "src",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(REPO / "runtime" / "config", runtime / "config")
        shutil.copytree(REPO / "runtime" / "corpus" / "questions", runtime / "corpus" / "questions")
        shutil.copyfile(HERE / "plugin.json", stage / "plugin.json")

        if seed:
            target = runtime / "corpus" / "pmal-study-seed.db"
            if keep_didactic:
                shutil.copyfile(seed, target)
                print("Banco-semente completo embutido (inclui material didático).")
            else:
                print("Banco-semente filtrado:", _public_seed(seed, target))
            print("Cobertura normativa:", json.dumps(_seed_report(target), ensure_ascii=False))
        else:
            print("AVISO: sem banco-semente. O plugin terá só as questões revisadas; "
                  "não haverá leis nem geração de questões inéditas.")

        output.mkdir(parents=True, exist_ok=True)
        archive = output / f"{SKILL}-{version}.zip"
        with ZipFile(archive, "w", ZIP_DEFLATED) as bundle:
            for path in _files(stage):
                info = ZipInfo(path.relative_to(stage).as_posix(), date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                bundle.writestr(info, path.read_bytes())
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
    parser.add_argument("--manter-didatico", action="store_true",
                        help="embute o banco completo, sem remover material de terceiros")
    parser.add_argument("--output", type=Path, default=HERE / "dist")
    args = parser.parse_args()
    seed = args.seed
    if seed is None and (REPO / "runtime" / "corpus" / "pmal-study-seed.db").is_file():
        seed = REPO / "runtime" / "corpus" / "pmal-study-seed.db"
    if seed is not None and not Path(seed).is_file():
        print(f"Banco-semente não encontrado: {seed}", file=sys.stderr)
        return 1
    build(Path(seed) if seed else None, args.manter_didatico, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
