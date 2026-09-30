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
  runtime/corpus/acervo/acervo-NNN.db  acervo de leis compactado em partes de até 4 MB (remontado no init)
```

## Gerar o .zip

```bash
python clients/chatgpt/build_plugin.py --leis "C:/Users/.../Desktop/PMAL" --seed "C:/Users/.../Desktop/PMAL/pmal-study.db"
```

- `--leis`: pasta com os PDFs oficiais das normas (sem subpastas). Instala `pypdf` automaticamente. O tópico do edital sai do número da lei no nome (`L9099.pdf`, `Lei nº 11.343.pdf`); `ESTATUTO PMAL` e `RDPMAL` são reconhecidos; CPM e CPPM precisam de `DEL1001`/`DEL1002` no nome.
- `--seed`: banco local (`pmal-study.db` ou `pmal-study-seed.db`). Passa por `scripts/build_public_seed.py` (mantém normas, edital e provas; remove texto de apostilas). `--manter-didatico` embute tudo (uso pessoal). O histórico de tentativas do banco vai junto.
- Pode usar um, outro ou ambos. Sem nenhum, o plugin traz só as questões revisadas.
- O build lista PDFs sem tópico reconhecido e tópicos do edital sem norma; limite de 100 MB. O acervo é compactado e dividido em partes (`--parte-mb`, padrão 4) e os textos saem com quebras de linha LF.
- Saída: `clients/chatgpt/dist/treinador-pmal-chatgpt-<versão>.zip`. Para atualizar, suba a `version` em `plugin.json` e use "Enviar nova versão".

## Uso

- Requer execução de código (modo Work/agente).
- O ambiente do ChatGPT é temporário: ao final da sessão, a skill entrega `pmal-progresso-*.db`. Anexe-o na conversa seguinte para manter histórico e revisões.
