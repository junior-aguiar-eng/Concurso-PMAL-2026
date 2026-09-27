#!/usr/bin/env bash
# Gera treinador-pmal-oficial.mcpb para instalação no Claude Desktop.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/mcp"
npm ci --omit=dev --no-audit --no-fund
cd "$ROOT"
npx -y @anthropic-ai/mcpb validate manifest.json
npx -y @anthropic-ai/mcpb pack . treinador-pmal-oficial.mcpb
# Restaura as dependências de desenvolvimento (vitest) para os testes locais.
(cd "$ROOT/mcp" && npm ci --no-audit --no-fund >/dev/null)
echo "Pacote gerado: $ROOT/treinador-pmal-oficial.mcpb"
