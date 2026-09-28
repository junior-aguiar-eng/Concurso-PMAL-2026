# Fluxos no ChatGPT

## Estudar por N minutos (widget)

Chame `pmal_open_study_panel` com modo `timed`, duração, disciplinas do recorte (se informadas) e identificador idempotente. O restante ocorre no widget; o modelo só atua quando receber mensagem de ação do painel.

## Trabalho enviado pelo painel

1. `pmal_claim_host_job` com o identificador.
2. `question_generation`: examine o brief em privado; refine com `pmal_search_evidence` se necessário; consulta externa só quando material, registrada com `pmal_register_live_evidence`. Formule caso concreto C/E, na dificuldade indicada, sem copiar enunciado ou exemplar, vinculando cada fundamento a evidência preparada. Conclua com `pmal_complete_generation_job`.
3. `dissection`: responda à dúvida com base exclusiva no contexto congelado; conclua com `pmal_complete_dissection_job`. A dissecação não altera gabarito, revisão ou exemplar.
4. No chat, uma linha neutra. Nada de brief, evidência, gabarito ou correção.

## Fluxo textual (sem widget ou por pedido do estudante)

1. `pmal_start_session` com o modo adequado → `pmal_next_question`.
2. Se vier questão, apresente só o enunciado público. Se vier `generation_brief`, formule o item, congele com `pmal_commit_generated_question` e apresente apenas a versão pública devolvida.
3. Colha C/E e confiança de 0 a 3 na mesma resposta do estudante; chame `pmal_submit_answer` com `attempt_id` único.
4. Apresente a correção integral. Uma questão por vez.

## Revisar erros

`pmal_get_review_queue` para contextualizar; depois `pmal_start_session` com modo `review`. Cada revisão gera item novo sobre o conceito vencido. Sem conceito vencido, informe e não crie sessão substituta.

## Treinar disciplina

`pmal_start_session` com modo `discipline`, duração e `disciplines` (somente as quatro admitidas). Rejeite disciplina externa sem aproximá-la de uma permitida.

## Diagnóstico

`pmal_start_session` com modo `diagnostic`; siga o fluxo textual.

## Simulado misto

`pmal_start_session` com modo `mixed_mock`, duração e filtros permitidos. Sem Informática. Não apresente métricas finais antes do encerramento.

## Painel de desempenho

`pmal_render_dashboard` para o widget; `pmal_get_dashboard` quando o estudante quiser os números em texto.

## Exportar resumo para Canvas

Somente por pedido explícito: `pmal_export_canvas_summary` e entrega do Markdown ao Canvas. Edições posteriores não são gravadas no histórico.
