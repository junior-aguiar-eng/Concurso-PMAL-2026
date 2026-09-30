import { CanvasExportSchema, CorpusRefreshSchema, DashboardSchema, DisciplineSchema, EndSessionSchema, EvidenceSearchSchema, ExemplarRecordSchema, GeneratedDraftSchema, GradingResultSchema, HostJobContextSchema, HostJobReferenceSchema, HostJobStatusSchema, LiveEvidenceSchema, PreparedNextItemSchema, PrivatePreparedNextItemSchema, PublicQuestionSchema, ReviewQueueSchema, SessionModeSchema, SessionSchema, SourceCheckSchema, StudyWorkspaceSchema, SubmitAndPrepareSchema, } from "./schemas.js";
import { z as schema } from "zod";
import { DASHBOARD_URI, STUDY_URI } from "./ui-resource.js";
/**
 * Erro de domínio sem structuredContent: clientes MCP validam structuredContent contra o
 * outputSchema mesmo quando isError é verdadeiro, e um envelope de erro nunca corresponde
 * ao schema de sucesso — o cliente descartaria a mensagem e acusaria -32602.
 */
function errorResult(code, message) {
    return {
        isError: true,
        content: [{ type: "text", text: `${code}: ${message}` }],
    };
}
function dataResult(data, output, summarize, options = {}) {
    const parsed = output.parse(data);
    const structuredContent = (Array.isArray(parsed) ? { items: parsed } : parsed);
    const content = [{ type: "text", text: summarize(parsed) }];
    // Clientes que repassam ao modelo só o content (e não o structuredContent) precisam
    // do JSON em texto para ler brief, enunciado e correção.
    if (options.includeJson)
        content.push({ type: "text", text: JSON.stringify(structuredContent) });
    return { content, structuredContent };
}
/** Ferramentas só do painel (visibility app) ou que renderizam UI mantêm o content enxuto. */
function isModelFacing(definition) {
    const ui = definition._meta?.ui;
    if (ui?.resourceUri)
        return false;
    return !(ui?.visibility && !ui.visibility.includes("model"));
}
export function createToolCatalog(core, options = {}) {
    const tool = (name, definition, command, output, summarize) => ({
        name,
        definition: {
            ...definition,
            outputSchema: output instanceof schema.ZodObject
                ? output.shape
                : { items: output },
        },
        handler: async (input) => {
            const response = await core.call(command, input);
            if (!response.ok)
                return errorResult(response.error.code, response.error.message);
            return dataResult(response.data, output, summarize, { includeJson: isModelFacing(definition) });
        },
    });
    const catalog = [
        tool("pmal_open_study_panel", {
            title: "Abrir ambiente de estudo PMAL",
            description: "Abre ou retoma o ambiente interativo principal em uma única chamada; pode iniciar uma nova sessão.",
            inputSchema: {
                mode: SessionModeSchema.optional(),
                duration_minutes: schema.number().int().min(1).max(480).optional(),
                disciplines: schema.array(DisciplineSchema).optional(),
                operation_id: schema.string().min(1).optional(),
            },
            annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false, idempotentHint: true },
            _meta: {
                ui: { resourceUri: STUDY_URI }, "openai/outputTemplate": STUDY_URI,
                "openai/toolInvocation/invoking": "Abrindo ambiente PMAL",
                "openai/toolInvocation/invoked": "Ambiente PMAL pronto",
            },
        }, "open-study-panel", StudyWorkspaceSchema, (data) => data.session ? `Ambiente aberto na sessão ${data.session.session_id}.` : "Ambiente aberto para configurar uma sessão."),
        tool("pmal_start_study_session", {
            title: "Iniciar sessão no ambiente PMAL",
            description: "Inicia uma sessão dentro do painel já aberto, sem renderizar outra superfície.",
            inputSchema: {
                mode: SessionModeSchema,
                duration_minutes: schema.number().int().min(1).max(480).optional(),
                disciplines: schema.array(DisciplineSchema).optional(),
                operation_id: schema.string().min(1),
            },
            annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false, idempotentHint: true },
            _meta: {
                ui: { visibility: ["app"] },
                "openai/visibility": "private",
                "openai/toolInvocation/invoking": "Iniciando sessão PMAL",
                "openai/toolInvocation/invoked": "Sessão PMAL pronta",
            },
        }, "open-study-panel", StudyWorkspaceSchema, (data) => data.session ? `Sessão ${data.session.session_id} iniciada.` : "Sessão PMAL pronta."),
        tool("pmal_prefetch_next_item", {
            title: "Pré-carregar uma questão PMAL",
            description: "Prepara exatamente um item posterior ao atual, sem revelar gabarito.",
            inputSchema: { session_id: schema.string().min(1), current_question_id: schema.string().min(1) },
            annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false, idempotentHint: true },
            _meta: { ui: { visibility: ["app"] }, "openai/visibility": "private" },
        }, "prefetch-next-item", PreparedNextItemSchema, (data) => data.kind === "question" ? "Próxima questão pré-carregada." : "Geração da próxima questão preparada."),
        tool("pmal_submit_and_prepare", {
            title: "Corrigir e preparar próxima questão",
            description: "Registra uma tentativa idempotente, libera a correção completa e devolve o item já pré-carregado.",
            inputSchema: {
                session_id: schema.string().min(1), question_id: schema.string().min(1),
                answer: schema.enum(["C", "E"]), confidence: schema.number().int().min(0).max(3),
                attempt_id: schema.string().min(1), error_pattern: schema.string().min(1).optional(),
            },
            annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false, idempotentHint: true },
            _meta: { ui: { visibility: ["app"] }, "openai/visibility": "private" },
        }, "submit-and-prepare", SubmitAndPrepareSchema, (data) => `Resposta ${data.grading.correct ? "correta" : "incorreta"}; próxima etapa preparada.`),
        tool("pmal_request_dissection_job", {
            title: "Solicitar dissecação da questão",
            description: "Cria um trabalho persistente de aprofundamento e devolve somente seu identificador opaco.",
            inputSchema: {
                request_id: schema.string().min(1), attempt_id: schema.string().min(1),
                user_message: schema.string().min(1),
            },
            annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false, idempotentHint: true },
            _meta: { ui: { visibility: ["app"] }, "openai/visibility": "private" },
        }, "request-dissection-job", HostJobReferenceSchema, (data) => `Dissecação ${data.request_id} aguardando processamento.`),
        tool("pmal_get_host_job_status", {
            title: "Consultar trabalho interno PMAL",
            description: "Consulta o estado público de um trabalho sem revelar evidências, gabarito ou instruções privadas.",
            inputSchema: { request_id: schema.string().min(1) },
            annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false, idempotentHint: true },
            _meta: { ui: { visibility: ["app"] }, "openai/visibility": "private" },
        }, "get-host-job-status", HostJobStatusSchema, (data) => `Trabalho ${data.request_id}: ${data.status}.`),
        tool("pmal_end_session", {
            title: "Encerrar sessão PMAL",
            description: "Encerra explicitamente a sessão e devolve seu resumo final.",
            inputSchema: { session_id: schema.string().min(1), operation_id: schema.string().min(1) },
            annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false, idempotentHint: true },
            _meta: { ui: { visibility: ["app"] }, "openai/visibility": "private" },
        }, "end-session", EndSessionSchema, (data) => `Sessão encerrada: ${data.attempts} respostas e ${data.correct} acertos.`),
        tool("pmal_claim_host_job", {
            title: "Obter contexto privado de trabalho PMAL",
            description: "Use ao receber um identificador de trabalho interno do painel. Obtém o contexto privado e marca o trabalho em processamento.",
            inputSchema: { request_id: schema.string().min(1) },
            annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false, idempotentHint: true },
            _meta: { ui: { visibility: ["model"] } },
        }, "claim-host-job", HostJobContextSchema, (data) => `Contexto privado do trabalho ${data.request_id} carregado.`),
        tool("pmal_complete_generation_job", {
            title: "Concluir geração interna PMAL",
            description: "Valida e persiste a questão produzida para um trabalho interno de geração.",
            inputSchema: { request_id: schema.string().min(1), draft: GeneratedDraftSchema, model: schema.string().min(1).optional() },
            annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false, idempotentHint: true },
            _meta: { ui: { visibility: ["model"] } },
        }, "complete-generation-job", HostJobStatusSchema, (data) => `Geração ${data.request_id} concluída.`),
        tool("pmal_complete_dissection_job", {
            title: "Concluir dissecação interna PMAL",
            description: "Persiste a resposta técnica produzida para um trabalho interno de dissecação.",
            inputSchema: { request_id: schema.string().min(1), assistant_message: schema.string().min(1), model: schema.string().min(1).optional() },
            annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false, idempotentHint: true },
            _meta: { ui: { visibility: ["model"] } },
        }, "complete-dissection-job", HostJobStatusSchema, (data) => `Dissecação ${data.request_id} concluída.`),
        tool("pmal_fail_host_job", {
            title: "Registrar falha de trabalho PMAL",
            description: "Encerra um trabalho interno com erro curto e acionável quando ele não puder ser concluído.",
            inputSchema: { request_id: schema.string().min(1), code: schema.string().min(1), message: schema.string().min(1) },
            annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false, idempotentHint: true },
            _meta: { ui: { visibility: ["model"] } },
        }, "fail-host-job", HostJobStatusSchema, (data) => `Trabalho ${data.request_id} encerrado com falha.`),
        tool("pmal_start_session", {
            title: "Iniciar sessão PMAL",
            description: "Use this when o estudante quiser iniciar diagnóstico, treino, revisão ou simulado PMAL Oficial.",
            inputSchema: {
                mode: SessionModeSchema,
                duration_minutes: schema.number().int().min(1).max(480).optional(),
                disciplines: schema.array(schema.string().min(1)).optional().describe("Somente: direito_penal_militar, direito_processual_penal_militar, legislacao_pmal ou conhecimentos_alagoas."),
            },
            annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false },
        }, "start-session", SessionSchema, (data) => `Sessão ${data.session_id} iniciada no modo ${data.mode}.`),
        tool("pmal_prepare_next_item", {
            title: "Preparar próxima questão PMAL",
            description: "Prepare evidências privadas para o modelo formular uma questão inédita; não mostre o brief ao estudante.",
            inputSchema: { session_id: schema.string().min(1) },
            annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false, idempotentHint: true },
        }, "prepare-next-item", PrivatePreparedNextItemSchema, (data) => data.kind === "generation"
            ? `Geração ${data.generation_brief?.job_id} preparada com ${data.generation_brief?.evidence.length} evidências.`
            : `Questão oficial ${data.question?.id} selecionada.`),
        tool("pmal_search_evidence", {
            title: "Refinar evidências PMAL",
            description: "Refine a busca local dentro da disciplina e do tópico já congelados no trabalho de geração.",
            inputSchema: { job_id: schema.string().min(1), query: schema.string().min(3) },
            annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false },
        }, "search-evidence", EvidenceSearchSchema, (data) => `${data.length} evidências locais recuperadas no mesmo escopo.`),
        tool("pmal_register_live_evidence", {
            title: "Registrar evidência institucional ao vivo",
            description: "Valide e congele um trecho encontrado em fonte institucional permitida para o trabalho de geração.",
            inputSchema: {
                job_id: schema.string().min(1), url: schema.url(), locator: schema.string().min(1),
                excerpt: schema.string().min(8), author: schema.string().min(1).optional(),
            },
            annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: true },
        }, "register-live-evidence", LiveEvidenceSchema, (data) => `Evidência ${data.source_kind} confirmada e congelada em ${data.id}.`),
        tool("pmal_commit_generated_question", {
            title: "Congelar questão inédita PMAL",
            description: "Valide, persista e libere somente a versão pública de uma questão inédita já fundamentada.",
            inputSchema: {
                job_id: schema.string().min(1),
                draft: GeneratedDraftSchema,
            },
            annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false },
        }, "commit-generated-question", PublicQuestionSchema, (data) => `Questão inédita ${data.id} congelada e pronta para apresentação.`),
        tool("pmal_refresh_corpus", {
            title: "Atualizar acervo PMAL agora",
            description: "Compare hashes e processe somente PDFs novos, alterados ou incompletos.",
            inputSchema: {},
            annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false, idempotentHint: true },
        }, "refresh-corpus", CorpusRefreshSchema, (data) => `Acervo atualizado: ${data.documents_processed} documento(s) processado(s), ${data.total_pages} páginas rastreadas.`),
        tool("pmal_set_exemplar", {
            title: "Gerenciar exemplar PMAL",
            description: "Aprove uma questão já corrigida como modelo estrutural ou revogue essa aprovação, sempre por decisão explícita do estudante.",
            inputSchema: {
                question_id: schema.string().min(1), action: schema.enum(["approve", "revoke"]),
                reason: schema.string().min(1).optional(),
            },
            annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false },
        }, "set-exemplar", ExemplarRecordSchema, (data) => `Exemplar ${data.question_id}: ${data.state}.`),
        tool("pmal_next_question", {
            title: "Obter próxima questão",
            description: "Use this when uma sessão aberta precisar apresentar a próxima questão sem revelar o gabarito.",
            inputSchema: { session_id: schema.string().min(1) },
            annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false },
        }, "next-question", PrivatePreparedNextItemSchema, (data) => data.kind === "question"
            ? `Próxima questão pronta: ${data.question?.id}.`
            : `A próxima questão exige geração controlada: ${data.generation_brief?.job_id}.`),
        tool("pmal_submit_answer", {
            title: "Corrigir resposta PMAL",
            description: "Use this when o estudante já tiver informado resposta C/E e confiança de 0 a 3.",
            inputSchema: {
                session_id: schema.string().min(1),
                question_id: schema.string().min(1),
                answer: schema.enum(["C", "E"]),
                confidence: schema.number().int().min(0).max(3),
                attempt_id: schema.string().min(1),
                error_pattern: schema.string().min(1).optional(),
            },
            annotations: {
                readOnlyHint: false,
                destructiveHint: false,
                openWorldHint: false,
                idempotentHint: true,
            },
        }, "submit-answer", GradingResultSchema, (data) => `Resposta ${data.correct ? "correta" : "incorreta"}; revisão em ${data.next_review_at}.`),
        tool("pmal_get_dashboard", {
            title: "Consultar painel PMAL",
            description: "Use this when o estudante quiser consultar desempenho, domínio e revisões vencidas.",
            inputSchema: {},
            annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false },
        }, "dashboard", DashboardSchema, (data) => `Painel atualizado: ${data.attempts_total} respostas e ${data.reviews_due} revisões vencidas.`),
        tool("pmal_get_review_queue", {
            title: "Consultar fila de revisão",
            description: "Use this when o estudante quiser ver quais questões precisam ser revistas e quando.",
            inputSchema: {},
            annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false },
        }, "review-queue", ReviewQueueSchema, (data) => `Fila de revisão consultada: ${data.length} questões.`),
        tool("pmal_export_canvas_summary", {
            title: "Exportar resumo em Markdown",
            description: "Use this when o estudante quiser um resumo Markdown do desempenho (Canvas, artefato ou documento).",
            inputSchema: {},
            annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false },
        }, "export-canvas", CanvasExportSchema, () => "Resumo Markdown gerado."),
        tool("pmal_check_official_source", {
            title: "Verificar fonte jurídica oficial",
            description: "Use this when uma sessão exigir consulta atual ao Planalto, STF ou STJ, com URL e citação explícitas.",
            inputSchema: { url: schema.url(), citation: schema.string().min(1) },
            annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: true },
        }, "check-official-source", SourceCheckSchema, (data) => `Fonte ${data.source_kind.toUpperCase()} verificada com estado ${data.status}.`),
        {
            name: "pmal_render_dashboard",
            definition: {
                title: "Exibir painel PMAL",
                description: "Use this only after pmal_get_dashboard to render the returned validated snapshot in the interactive panel.",
                inputSchema: DashboardSchema.shape,
                outputSchema: DashboardSchema.shape,
                annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false, idempotentHint: true },
                _meta: {
                    ui: { resourceUri: DASHBOARD_URI },
                    "openai/outputTemplate": DASHBOARD_URI,
                    "openai/toolInvocation/invoking": "Abrindo painel PMAL",
                    "openai/toolInvocation/invoked": "Painel PMAL atualizado",
                },
            },
            handler: async (input) => dataResult(input, DashboardSchema, (data) => `Painel exibido: ${data.attempts_total} respostas e ${data.reviews_due} revisões vencidas.`),
        },
    ];
    return options.includeRender === false
        ? catalog.filter((entry) => !["pmal_open_study_panel", "pmal_start_study_session", "pmal_render_dashboard"].includes(entry.name))
        : catalog;
}
