# Treinador PMAL Oficial (Concurso PMAL 2026)

Tutor adaptativo orientado por evidências para o concurso de Oficial da Polícia Militar de Alagoas (PMAL 2026): questões Cebraspe oficiais e inéditas geradas a partir do acervo, ambiente de estudo interativo e revisão espaçada. Funciona no Codex e no Claude Desktop.

---

## 🎯 Disciplinas Foco

- **Direito Penal Militar**
- **Direito Processual Penal Militar**
- **Legislação PMAL**
- **Conhecimentos de Alagoas**

---

## 🏗️ Estrutura do Projeto

- **`.codex-plugin/`**: Metadados do plugin para integração com o agente.
- **`mcp/`**: Servidor MCP (`@pmal-oficial/mcp-server`) com ferramentas para gestão de sessões, tentativas e fontes oficiais.
- **`runtime/`**:
  - `src/pmal_study/`: Motor em Python (SQLite, agendador de revisões espaçadas, sessões adaptativas, importador de questões Cebraspe, relatórios).
  - `corpus/questions/reviewed/`: Questões estruturadas em JSONL (bancas Cebraspe / Soldado / Oficial PMAL).
  - `corpus/pmal-study-seed.db`: acervo indexado local (**não versionado**).
  - `config/`: Edital verticalizado e mapeamento programático (`syllabus_official.json`).
- **`skills/`**: Habilidades do agente (`treinador-pmal-oficial`), fluxos de treino e regras de condução pedagógica.
- **`ui/`**: Ambiente de estudo interativo (`study.js`) e painel de desempenho (`dashboard.js`).

---

## 🚀 Como Executar

### Pré-requisitos
- Node.js >= 22
- Python >= 3.12 (ou uv)

### Claude Desktop
Instale a extensão `.mcpb` gerada por `./scripts/build-desktop-extension.sh` ou configure manualmente. Veja [docs/CLAUDE_DESKTOP.md](docs/CLAUDE_DESKTOP.md).

### Configuração do MCP
O servidor MCP expõe as ferramentas `pmal_*`:
- `pmal_open_study_panel` (ambiente interativo) e ferramentas de trabalho do painel (`pmal_claim_host_job`, `pmal_complete_*`)
- Fluxo textual: `pmal_start_session`, `pmal_next_question`, `pmal_commit_generated_question`, `pmal_submit_answer`
- Acervo: `pmal_search_evidence`, `pmal_register_live_evidence`, `pmal_refresh_corpus`
- Relatórios e painel de estudos.
