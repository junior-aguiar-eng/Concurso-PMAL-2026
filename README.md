# Treinador PMAL Oficial (Concurso PMAL 2026)

Plugin dedicado para preparação e estudo adaptativo do concurso de Oficial da Polícia Militar de Alagoas (PMAL 2026).

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
  - `config/`: Edital verticalizado e mapeamento programático (`syllabus_official.json`).
- **`skills/`**: Habilidades do agente (`treinador-pmal-oficial`), fluxos de treino e regras de condução pedagógica.
- **`ui/`**: Interface e componentes do painel do estudante (`dashboard.js`).

---

## 🚀 Como Executar

### Pré-requisitos
- Node.js >= 24
- Python >= 3.12

### Configuração do MCP
O servidor MCP expõe as ferramentas `pmal_*`:
- `pmal_next_question`
- `pmal_submit_answer`
- `pmal_check_official_source`
- Relatórios e painel de estudos.
