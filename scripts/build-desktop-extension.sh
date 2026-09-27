#!/usr/bin/env bash
# Gera treinador-pmal-oficial.mcpb para instalação no Claude Desktop.
# Uso: PMAL_SEED_DB=/caminho/pmal-study-seed.db ./scripts/build-desktop-extension.sh
# O banco-semente (acervo indexado) não é versionado; sem ele, a extensão parte só das questões revisadas.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SEED_TARGET="$ROOT/runtime/corpus/pmal-study-seed.db"
if [[ -n "${PMAL_SEED_DB:-}" ]]; then
  cp "$PMAL_SEED_DB" "$SEED_TARGET"
fi
[[ -f "$SEED_TARGET" ]] || echo "Aviso: sem banco-semente; o acervo pesquisável ficará vazio."
cd "$ROOT/mcp"
npm ci --no-audit --no-fund
npm run build
npm ci --omit=dev --no-audit --no-fund
cd "$ROOT"
npx -y @anthropic-ai/mcpb validate manifest.json
npx -y @anthropic-ai/mcpb pack . treinador-pmal-oficial.mcpb
# Restaura as dependências de desenvolvimento para os testes locais.
(cd "$ROOT/mcp" && npm ci --no-audit --no-fund >/dev/null)
echo "Pacote gerado: $ROOT/treinador-pmal-oficial.mcpb"
