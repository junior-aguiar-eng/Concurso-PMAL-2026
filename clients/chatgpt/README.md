# Cliente ChatGPT

Skill exclusiva do ChatGPT, isolada da skill do Claude/Codex (`skills/treinador-pmal-oficial/`). Nada aqui é lido pelo `.codex-plugin`, pelo Claude Desktop nem pelo pacote `.mcpb`.

- `treinador-pmal-chatgpt/`: skill (SKILL.md + referências).
- `build-skill.sh`: gera `dist/treinador-pmal-chatgpt.zip` para upload em ChatGPT → Skills.
- Conexão do servidor MCP: [treinador-pmal-chatgpt/references/conexao.md](treinador-pmal-chatgpt/references/conexao.md).

O servidor MCP é o mesmo (`mcp/`), em modo `--transport http`; ele já publica os metadados `openai/*` do widget.
