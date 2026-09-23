import { CanvasExportSchema, DashboardSchema, DisciplineSchema, GradingResultSchema, PublicQuestionSchema, ReviewQueueSchema, SessionModeSchema, SessionSchema, SourceCheckSchema, } from "./schemas.js";
import { z as schema } from "zod";
function errorResult(code, message) {
    return {
        isError: true,
        content: [{ type: "text", text: `${code}: ${message}` }],
        structuredContent: { ok: false, error: { code, message } },
    };
}
function dataResult(data, output, summarize) {
    const parsed = output.parse(data);
    const structuredContent = (Array.isArray(parsed) ? { items: parsed } : parsed);
    return {
        content: [{ type: "text", text: summarize(parsed) }],
        structuredContent,
    };
}
export function createToolCatalog(core) {
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
            return dataResult(response.data, output, summarize);
        },
    });
    return [
        tool("pmal_start_session", {
            title: "Iniciar sessão PMAL",
            description: "Use this when o estudante quiser iniciar diagnóstico, treino, revisão ou simulado PMAL Oficial.",
            inputSchema: {
                mode: SessionModeSchema,
                duration_minutes: schema.number().int().min(1).max(480).optional(),
                disciplines: schema.array(DisciplineSchema).optional(),
            },
            annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false },
        }, "start-session", SessionSchema, (data) => `Sessão ${data.session_id} iniciada no modo ${data.mode}.`),
        tool("pmal_next_question", {
            title: "Obter próxima questão",
            description: "Use this when uma sessão aberta precisar apresentar a próxima questão sem revelar o gabarito.",
            inputSchema: { session_id: schema.string().min(1) },
            annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false },
        }, "next-question", PublicQuestionSchema, (data) => `Próxima questão: ${data.id}, tópico ${data.topic_id}.`),
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
            title: "Exportar resumo para Canvas",
            description: "Use this when o estudante quiser um resumo Markdown editável no Canvas.",
            inputSchema: {},
            annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false },
        }, "export-canvas", CanvasExportSchema, () => "Resumo Markdown gerado para o Canvas."),
        tool("pmal_check_official_source", {
            title: "Verificar fonte jurídica oficial",
            description: "Use this when uma sessão exigir consulta atual ao Planalto, STF ou STJ, com URL e citação explícitas.",
            inputSchema: { url: schema.url(), citation: schema.string().min(1) },
            annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: true },
        }, "check-official-source", SourceCheckSchema, (data) => `Fonte ${data.source_kind.toUpperCase()} verificada com estado ${data.status}.`),
    ];
}
