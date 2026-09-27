# Uso no Claude Desktop

## Requisitos

- **Python 3.12+** (recomendado; o núcleo não tem dependências externas) **ou** [uv](https://docs.astral.sh/uv/).
- Node.js não é necessário na opção A: o Claude Desktop traz seu próprio Node.

## Opção A — Extensão `.mcpb` (recomendada)

1. Gere o pacote: `./scripts/build-desktop-extension.sh` (ou use o `.mcpb` já distribuído).
2. Claude Desktop → **Configurações → Extensões** → arraste `treinador-pmal-oficial.mcpb` (ou dê duplo clique).
3. Preencha:
   - **Python 3.12+**: caminho do executável (`which python3` / `where python`). Se vazio, usa o uv.
   - **Pasta de dados**: padrão `~/.pmal-study`. O histórico (SQLite) fica aqui e sobrevive a atualizações.

## Opção B — Configuração manual

Clone o repositório, rode `npm ci` em `mcp/` e edite `claude_desktop_config.json`
(macOS: `~/Library/Application Support/Claude/`; Windows: `%APPDATA%\Claude\`):

```json
{
  "mcpServers": {
    "treinador-pmal-oficial": {
      "command": "node",
      "args": ["/CAMINHO/Concurso-PMAL-2026/mcp/dist/index.js", "--transport", "stdio"],
      "env": {
        "PMAL_PYTHON": "/usr/local/bin/python3",
        "PMAL_DATABASE": "/Users/SEU_USUARIO/.pmal-study/pmal-study.db"
      }
    }
  }
}
```

Reinicie o Claude Desktop. No Windows, use barras duplas (`C:\\Python312\\python.exe`).

## Variáveis de ambiente

| Variável | Efeito |
|---|---|
| `PMAL_PYTHON` | Executa o núcleo direto com esse Python (dispensa o uv). |
| `PMAL_UV` | Caminho absoluto do uv, quando `PMAL_PYTHON` não é usado. |
| `PMAL_DATABASE` | Caminho absoluto do banco SQLite. Padrão: `runtime/.pmal-study/pmal-study.db`. |
| `PMAL_PROJECT_ROOT` | Pasta `runtime/`. Padrão: a que acompanha o servidor. |
| `PMAL_TIMEOUT_MS` | Limite por chamada da CLI (padrão 30000). |

## Uso

- Menu **+ → Treinador PMAL Oficial** oferece os prompts: *Estudar por tempo*, *Revisar pendências*, *Diagnóstico*, *Simulado misto* e *Painel de desempenho*.
- Ou peça em linguagem natural: "Quero treinar Direito Penal Militar por 30 minutos."
- As regras de condução (uma questão por vez, C/E + confiança 0–3, só as quatro disciplinas) são enviadas pelo próprio servidor, sem depender da pasta `skills/`.

## Problemas comuns

- **"Executável uv não encontrado"**: o Desktop não herda o PATH do terminal. Informe o caminho do Python (ou do uv).
- **Logs**: macOS `~/Library/Logs/Claude/mcp-server-treinador-pmal-oficial.log`; Windows `%APPDATA%\Claude\logs\`.
