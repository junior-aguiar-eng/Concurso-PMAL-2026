#!/usr/bin/env bash
# Empacota a skill do ChatGPT em dist/treinador-pmal-chatgpt.zip para upload.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$DIR/dist"
rm -f "$DIR/dist/treinador-pmal-chatgpt.zip"
(cd "$DIR" && zip -rq dist/treinador-pmal-chatgpt.zip treinador-pmal-chatgpt)
echo "Pacote gerado: $DIR/dist/treinador-pmal-chatgpt.zip"
