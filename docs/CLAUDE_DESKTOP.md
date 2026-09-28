# Uso no Claude Desktop

## Requisitos

- **Python 3.12+** (recomendado; o núcleo não tem dependências externas) **ou** [uv](https://docs.astral.sh/uv/).
- Opcional, só para indexar PDFs novos: `pdftotext` (Poppler) no PATH.
- Node.js não é necessário: o Claude Desktop traz o seu.

## Instalação (extensão `.mcpb`)

1. Gere o pacote com o banco-semente do acervo:
   `PMAL_SEED_DB=/caminho/pmal-study-seed.db ./scripts/build-desktop-extension.sh`
2. Claude Desktop → **Configurações → Extensões** → arraste `treinador-pmal-oficial.mcpb`.
3. Preencha:
   - **Python 3.12+**: caminho do executável (`where python` no Windows). Se vazio, usa o uv.
   - **Pasta de dados**: padrão `~/.pmal-study`. Recebe uma cópia do banco-semente na primeira execução e guarda seu histórico; sobrevive a atualizações da extensão.
   - **Pasta dos PDFs** (opcional): usada por “Atualizar acervo” para indexar PDFs novos ou alterados.

> O banco-semente contém o texto indexado de materiais de terceiros e **não é versionado** no repositório público.

## Uso

- Menu **+ → Treinador PMAL Oficial**: *Estudar por tempo*, *Revisar pendências*, *Diagnóstico*, *Simulado misto*, *Estudar no chat (sem painel)* e *Painel de desempenho*.
- O padrão é o **ambiente de estudo interativo** (`pmal_open_study_panel`): o ciclo questão → C/E + confiança → correção acontece no painel. Pedidos do painel (gerar questão inédita, dissecar dúvida) chegam ao Claude como identificadores de trabalho, processados por `pmal_claim_host_job`.
- Se o painel não aparecer, use *Estudar no chat*: o Claude conduz pelo fluxo textual, gerando questões inéditas a partir das evidências do acervo e congelando-as antes de apresentar.
- As regras de condução vão nas instruções do servidor; o Desktop não depende da pasta `skills/`.

## Organização da pasta dos PDFs

A classificação usa o caminho do arquivo:

| Onde colocar | Classificação |
|---|---|
| `Legislação Oficial/` (íntegra da lei, ex.: `Lei 8.072-1990 - Hediondos.pdf`) | cópia oficial; tópico pelo número da lei |
| `CPM - DEL1001Compilado.pdf`, `CPPM - DEL1002Compilado.pdf` | cópia oficial; tópico pela faixa de artigos |
| `CPM/`, `CPPM/`, `Legislação PMAL/`, `Conhecimentos AL/` | material didático da disciplina |
| Outras pastas sem disciplina reconhecível | indexadas, mas **excluídas** das evidências |

Material didático exige confirmação em fonte oficial antes de fundamentar questão; cópia oficial dispensa.
Atualizar o acervo com uma pasta que não contém os PDFs antigos não apaga o que já foi indexado.

## Versão compartilhável

O banco-semente pessoal contém texto de materiais de terceiros e **não deve ser distribuído**. Para compartilhar, gere um banco só com fontes públicas (normas oficiais, edital e provas) e empacote com ele:

```bash
python3 scripts/build_public_seed.py pmal-study-seed.db pmal-study-seed-publico.db
PMAL_SEED_DB=pmal-study-seed-publico.db ./scripts/build-desktop-extension.sh
```

Materiais didáticos ficam apenas como referência de página (sem texto). Quem tiver o PDF original o reindexa em “Atualizar acervo”.

## Configuração manual (alternativa)

`claude_desktop_config.json` (Windows: `%APPDATA%\Claude\`; macOS: `~/Library/Application Support/Claude/`):

```json
{
  "mcpServers": {
    "treinador-pmal-oficial": {
      "command": "node",
      "args": ["C:\\CAMINHO\\Concurso-PMAL-2026\\mcp\\dist\\index.js", "--transport", "stdio"],
      "env": {
        "PMAL_PYTHON": "C:\\CAMINHO\\python.exe",
        "PMAL_DATA_DIR": "C:\\Users\\SEU_USUARIO\\.pmal-study"
      }
    }
  }
}
```

Coloque o banco-semente em `runtime/corpus/pmal-study-seed.db` antes do primeiro uso.

## Variáveis de ambiente

| Variável | Efeito |
|---|---|
| `PMAL_PYTHON` | Executa o worker direto com esse Python (dispensa o uv). |
| `PMAL_UV` | Caminho absoluto do uv, quando `PMAL_PYTHON` não é usado. |
| `PMAL_DATA_DIR` | Pasta do banco (padrão `~/.codex/state/treinador-pmal-oficial`, compatível com o Codex). |
| `PMAL_DATABASE` | Caminho explícito do banco (dispensa a cópia do banco-semente). |
| `PMAL_SOURCE_ROOT` | Pasta dos PDFs para “Atualizar acervo”. |
| `PMAL_PROJECT_ROOT` | Pasta `runtime/` (padrão: a que acompanha o servidor). |
| `PMAL_TIMEOUT_MS` | Limite por chamada ao worker (padrão 30000). |

## Problemas comuns

- **“Executável uv não encontrado”**: o Desktop não herda o PATH; informe o caminho do Python.
- **Painel não aparece**: use o prompt *Estudar no chat*.
- **Logs**: Windows `%APPDATA%\Claude\logs\mcp-server-treinador-pmal-oficial.log`; macOS `~/Library/Logs/Claude/`.
