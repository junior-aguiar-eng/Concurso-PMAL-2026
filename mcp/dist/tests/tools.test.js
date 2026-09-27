import { describe, expect, it } from "vitest";
import { createToolCatalog } from "../tools.js";
describe("catálogo MCP", () => {
    it("expõe ferramentas de dados sem vínculo de UI", () => {
        const catalog = createToolCatalog({
            call: async () => ({ ok: true, data: {} }),
        }, { includeRender: false });
        expect(catalog.map((tool) => tool.name)).toEqual([
            "pmal_start_session",
            "pmal_next_question",
            "pmal_submit_answer",
            "pmal_get_dashboard",
            "pmal_get_review_queue",
            "pmal_export_canvas_summary",
            "pmal_check_official_source",
        ]);
        expect(catalog.every((tool) => !("_meta" in tool.definition))).toBe(true);
        expect(catalog.every((tool) => tool.definition.outputSchema)).toBe(true);
    });
    it("devolve conteúdo legível e structuredContent completo", async () => {
        const catalog = createToolCatalog({
            call: async () => ({
                ok: true,
                data: { attempts_total: 2, correct_total: 1, accuracy: 0.5,
                    review_total: 1, reviews_due: 0, mastery_by_discipline: {}, study_bank: 19 },
            }),
        });
        const dashboard = catalog.find((tool) => tool.name === "pmal_get_dashboard");
        const result = await dashboard?.handler({});
        expect(result?.content[0]).toEqual({
            type: "text",
            text: "Painel atualizado: 2 respostas e 0 revisões vencidas.",
        });
        expect(result?.structuredContent).toMatchObject({ attempts_total: 2, accuracy: 0.5 });
        expect(JSON.parse(result?.content[1].text)).toEqual(result?.structuredContent);
    });
    it("inclui a ferramenta de renderização do painel por padrão", () => {
        const catalog = createToolCatalog({ call: async () => ({ ok: true, data: {} }) });
        expect(catalog.at(-1)?.name).toBe("pmal_render_dashboard");
    });
    it("mapeia falha Python para erro MCP estável", async () => {
        const catalog = createToolCatalog({
            call: async () => ({
                ok: false,
                error: { code: "session_not_found", message: "Sessão não localizada." },
            }),
        });
        const next = catalog.find((tool) => tool.name === "pmal_next_question");
        const result = await next?.handler({ session_id: "ausente" });
        expect(result?.isError).toBe(true);
        expect(result?.structuredContent).toEqual({
            ok: false,
            error: { code: "session_not_found", message: "Sessão não localizada." },
        });
    });
});
