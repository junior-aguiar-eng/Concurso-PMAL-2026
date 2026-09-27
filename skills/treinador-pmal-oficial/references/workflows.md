# Fluxos conversacionais

## Estudar por N minutos

Entrada: duração e, opcionalmente, disciplinas do recorte. Chame `pmal_open_study_panel` com modo `timed`, duração, disciplinas e identificador idempotente. O restante ocorre dentro do painel.

## Ciclo controlado de uma questão

1. O painel recebe ou prepara a questão pública e pré-carrega exatamente um item posterior com `pmal_prefetch_next_item`.
2. Se `kind=question`, apresente o `PublicQuestion` sem fonte ou comentário antecipatório.
3. Se `kind=generation`, examine o brief privadamente. Refine com `pmal_search_evidence` se necessário. Faça consulta externa apenas quando a atualização for material e registre o trecho confirmado com `pmal_register_live_evidence`.
4. Formule um caso concreto C/E de dificuldade indicada, sem copiar enunciado ou exemplar. Vincule cada fundamento a evidência preparada e chame `pmal_commit_generated_question`.
5. Apresente somente o `PublicQuestion` devolvido e recolha C/E mais confiança de 0 a 3.
6. Chame `pmal_submit_and_prepare`; apresente integralmente a correção e mantenha o item pré-carregado para avanço imediato.
7. Depois da correção, informe de modo discreto que a questão pode ser aprovada como modelo. Só chame `pmal_set_exemplar` se o estudante decidir fazê-lo.
8. Perguntas de aprofundamento criam um trabalho persistente por `pmal_request_dissection_job`; o painel envia somente o identificador, o modelo obtém o contexto por `pmal_claim_host_job` e persiste a resposta por `pmal_complete_dissection_job`.

## Revisar erros

Chame `pmal_get_review_queue` para contextualizar a pendência e `pmal_start_session` com modo `review`. Cada revisão deve gerar novo item sobre o conceito vencido; não reapresente literalmente a questão anterior nem exemplar. Se não houver conceito vencido, informe isso sem criar sessão substituta.

## Treinar disciplina

Entrada obrigatória: uma ou mais das quatro disciplinas admitidas. Chame `pmal_start_session` com modo `discipline`, duração e `disciplines`; depois, execute o ciclo controlado. Rejeite disciplina externa sem aproximá-la de uma disciplina permitida.

## Diagnóstico

Chame `pmal_start_session` com modo `diagnostic`. O núcleo prioriza conceitos ainda não avaliados e intercala itens oficiais quando pedagogicamente útil. Execute o ciclo controlado.

## Simulado misto

Chame `pmal_start_session` com modo `mixed_mock`, duração e filtros permitidos quando fornecidos. Não acrescente Informática ao conjunto. Execute o ciclo controlado sem apresentar métricas finais antes do encerramento.

## Mostrar painel

Chame `pmal_open_study_panel` sem parâmetros para retomar o ambiente integrado. Métricas, edital, erros e acervo são áreas secundárias; ações continuam validadas pelas ferramentas e nunca alteram estado apenas na interface.

## Exportar resumo para Canvas

Somente mediante pedido explícito, chame `pmal_export_canvas_summary` e entregue o Markdown retornado à superfície de Canvas. O fluxo termina na exportação; não interprete edições do Canvas como gravações no histórico.

## Consulta oficial durante uma sessão

Quando o fundamento exigir atualização externa, identifique URL, autoria, localizador e trecho em fonte permitida. Chame `pmal_register_live_evidence` dentro do trabalho de geração. Se o trecho não for confirmado, a consulta falhar ou houver conflito material, escolha outro tópico ou suspenda a geração; não improvise fundamento.
