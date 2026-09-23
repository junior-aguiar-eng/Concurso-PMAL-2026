# Fluxos conversacionais

## Estudar por N minutos

Entrada: duração e, opcionalmente, disciplinas do recorte. Chame `pmal_start_session` com modo `timed`; em seguida, alterne `pmal_next_question` → coleta de C/E e confiança → `pmal_submit_answer`. Apresente a correção devolvida e repita até `session_complete` ou `session_expired`.

## Revisar erros

Chame `pmal_get_review_queue` para contextualizar a pendência e `pmal_start_session` com modo `review`. Se não houver questões elegíveis, informe isso sem criar sessão substituta. Havendo, siga o ciclo questão → resposta/confiança → correção até o terminal da sessão.

## Treinar disciplina

Entrada obrigatória: uma ou mais das quatro disciplinas admitidas. Chame `pmal_start_session` com modo `discipline`, duração e `disciplines`; depois, execute o ciclo comum. Rejeite disciplina externa sem aproximá-la de uma disciplina permitida.

## Diagnóstico

Chame `pmal_start_session` com modo `diagnostic`. O núcleo seleciona apenas questões ainda não respondidas. Execute uma questão por vez até o terminal da sessão.

## Simulado misto

Chame `pmal_start_session` com modo `mixed_mock`, duração e filtros permitidos quando fornecidos. Não acrescente Informática ao conjunto. Execute o ciclo comum sem apresentar métricas finais antes do encerramento.

## Mostrar painel

Chame `pmal_get_dashboard`. Explique apenas os valores recebidos. Quando a ferramenta de renderização estiver disponível e a visualização for útil, use o snapshot recebido como entrada de `pmal_render_dashboard`; a ferramenta de renderização não substitui a consulta de dados.

## Exportar resumo para Canvas

Somente mediante pedido explícito, chame `pmal_export_canvas_summary` e entregue o Markdown retornado à superfície de Canvas. O fluxo termina na exportação; não interprete edições do Canvas como gravações no histórico.

## Consulta oficial durante uma sessão

Quando o fundamento exigir atualização externa, identifique primeiro a URL oficial e o localizador. Chame `pmal_check_official_source` com `url` e `citation`. Se o estado for `changed` ou a consulta falhar, suspenda a afirmação dependente da fonte e não modifique questão ou gabarito.
