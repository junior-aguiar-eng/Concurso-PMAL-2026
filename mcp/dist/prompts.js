import { z } from "zod";
/**
 * Regras de condução entregues pelo próprio servidor. Clientes como o Claude Desktop
 * não carregam a pasta skills/, então o protocolo pedagógico precisa viajar aqui.
 */
export const SERVER_INSTRUCTIONS = [
    "Treinador PMAL Oficial: as ferramentas pmal_* e o SQLite local são a fonte autoritativa; a conversa não guarda histórico.",
    "Disciplinas admitidas (exclusivamente): direito_penal_militar, direito_processual_penal_militar, legislacao_pmal, conhecimentos_alagoas. Rejeite qualquer outra, inclusive em simulado.",
    "Modos de sessão: diagnostic (itens inéditos), timed (estudo por N minutos), discipline (exige disciplines), review (fila de revisão), mixed_mock (simulado misto).",
    "Ciclo: pmal_next_question → apresente o enunciado sem antecipar gabarito → obtenha C/E e confiança 0-3 → pmal_submit_answer com attempt_id único (reutilize-o apenas ao repetir a mesma submissão). Uma questão por vez, até session_complete ou session_expired.",
    "Na correção, exponha o gabarito, o fundamento e a fonte devolvidos pela ferramenta, sem alterá-los.",
    "pmal_check_official_source: apenas URL HTTPS do Planalto, STF ou STJ, com citação explícita; estado changed ou falha suspende a afirmação dependente.",
    "Painel: chame pmal_get_dashboard e, se útil, repasse o snapshot a pmal_render_dashboard.",
].join("\n");
const DISCIPLINES_HINT = "direito_penal_militar, direito_processual_penal_militar, legislacao_pmal, conhecimentos_alagoas";
function userPrompt(text) {
    return { messages: [{ role: "user", content: { type: "text", text } }] };
}
export const PROMPTS = [
    {
        name: "pmal_estudar",
        title: "Estudar por tempo",
        description: "Sessão cronometrada de questões C/E, opcionalmente filtrada por disciplina.",
        argsSchema: {
            minutos: z.string().describe("Duração em minutos (ex.: 30)."),
            disciplinas: z.string().optional().describe(`Opcional, separadas por vírgula: ${DISCIPLINES_HINT}.`),
        },
        build: ({ minutos, disciplinas }) => userPrompt(`Inicie uma sessão PMAL no modo "${disciplinas ? "discipline" : "timed"}" com duration_minutes=${Number.parseInt(minutos, 10) || 30}` +
            (disciplinas ? ` e disciplines=[${disciplinas.split(",").map((item) => `"${item.trim()}"`).join(", ")}]` : "") +
            ". Apresente uma questão por vez, colha C/E e confiança de 0 a 3 antes de corrigir."),
    },
    {
        name: "pmal_revisar",
        title: "Revisar pendências",
        description: "Consulta a fila de revisão espaçada e conduz a sessão de revisão.",
        build: () => userPrompt("Consulte minha fila de revisão PMAL e, havendo questões elegíveis, inicie uma sessão no modo \"review\". Se não houver, apenas informe."),
    },
    {
        name: "pmal_diagnostico",
        title: "Diagnóstico",
        description: "Sessão só com questões ainda não respondidas.",
        build: () => userPrompt("Inicie uma sessão PMAL no modo \"diagnostic\" e conduza uma questão por vez até o encerramento."),
    },
    {
        name: "pmal_simulado",
        title: "Simulado misto",
        description: "Simulado cronometrado com as quatro disciplinas do recorte.",
        argsSchema: { minutos: z.string().describe("Duração em minutos (ex.: 60).") },
        build: ({ minutos }) => userPrompt(`Inicie um simulado PMAL no modo "mixed_mock" com duration_minutes=${Number.parseInt(minutos, 10) || 60}. Não mostre métricas antes do encerramento.`),
    },
    {
        name: "pmal_painel",
        title: "Painel de desempenho",
        description: "Desempenho por disciplina, cobertura do edital e revisões vencidas.",
        build: () => userPrompt("Mostre meu painel de estudos PMAL: consulte pmal_get_dashboard e, se disponível, exiba com pmal_render_dashboard."),
    },
];
export function registerPrompts(server) {
    for (const prompt of PROMPTS) {
        server.registerPrompt(prompt.name, {
            title: prompt.title,
            description: prompt.description,
            ...(prompt.argsSchema ? { argsSchema: prompt.argsSchema } : {}),
        }, async (args) => prompt.build(args ?? {}));
    }
}
