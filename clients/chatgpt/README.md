# Plugin do ChatGPT

Plugin instalável por upload (ChatGPT → Plugins → Adicionar → enviar `.zip`), isolado da versão Claude/Codex (`skills/`, `.codex-plugin/`, `manifest.json`, `mcp/`). Não usa servidor MCP: o motor Python roda no ambiente de execução do ChatGPT.

## Conteúdo do pacote

```
plugin.json
skills/treinador-pmal-chatgpt/
  SKILL.md                 instruções (autossuficientes)
  agents/openai.yaml
  scripts/pmal.py          ponte com o motor
  runtime/                 copiado de runtime/ no build: src, config, questões revisadas
  runtime/corpus/pmal-study-seed.db   acervo de leis (se informado no build)
```

## Gerar o .zip

```bash
python clients/chatgpt/build_plugin.py --seed CAMINHO/pmal-study-seed.db
```

- Sem `--seed` (nem `PMAL_SEED_DB`), o plugin traz só as questões revisadas: sem leis e sem questões inéditas.
- Por padrão, o banco passa por `scripts/build_public_seed.py`: mantém normas oficiais, edital e provas e remove o texto de apostilas de terceiros. `--manter-didatico` embute tudo (uso pessoal).
- O build informa os tópicos do edital sem norma indexada; limite de 100 MB.
- Saída: `clients/chatgpt/dist/treinador-pmal-chatgpt-<versão>.zip`. Para atualizar, suba a `version` em `plugin.json` e use "Enviar nova versão".

## Uso

- Requer execução de código (modo Work/agente).
- O ambiente do ChatGPT é temporário: ao final da sessão, a skill entrega `pmal-progresso-*.db`. Anexe-o na conversa seguinte para manter histórico e revisões.
