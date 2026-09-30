import { describe, expect, it } from "vitest";

import { createToolCatalog } from "../src/tools.js";

describe("catálogo MCP", () => {
  it("mantém ferramentas de dados desacopladas e reserva UI ao renderizador", () => {
    const catalog = createToolCatalog({
      call: async () => ({ ok: true, data: {} }),
    });

    expect(catalog.map((tool) => tool.name)).toEqual([
      "pmal_open_study_panel",
      "pmal_start_study_session",
      "pmal_prefetch_next_item",
      "pmal_submit_and_prepare",
      "pmal_request_dissection_job",
      "pmal_get_host_job_status",
      "pmal_end_session",
      "pmal_claim_host_job",
      "pmal_complete_generation_job",
      "pmal_complete_dissection_job",
      "pmal_fail_host_job",
      "pmal_start_session",
      "pmal_prepare_next_item",
      "pmal_search_evidence",
      "pmal_register_live_evidence",
      "pmal_commit_generated_question",
      "pmal_refresh_corpus",
      "pmal_set_exemplar",
      "pmal_next_question",
      "pmal_submit_answer",
      "pmal_get_dashboard",
      "pmal_get_review_queue",
      "pmal_export_canvas_summary",
      "pmal_check_official_source",
      "pmal_render_dashboard",
    ]);
    expect(catalog[0]?.definition._meta).toMatchObject({ ui: { resourceUri: "ui://pmal/study/v4.html" } });
    expect(catalog[1]?.definition._meta).toMatchObject({
      ui: { visibility: ["app"] }, "openai/visibility": "private",
    });
    expect(catalog[1]?.definition._meta).not.toHaveProperty("ui.resourceUri");
    expect(catalog.at(-1)?.definition._meta).toMatchObject({
      ui: { resourceUri: "ui://pmal/dashboard/v1.html" },
    });
    for (const name of [
      "pmal_start_study_session", "pmal_prefetch_next_item", "pmal_submit_and_prepare",
      "pmal_request_dissection_job", "pmal_get_host_job_status", "pmal_end_session",
    ]) {
      expect(catalog.find((tool) => tool.name === name)?.definition._meta)
        .toMatchObject({ ui: { visibility: ["app"] } });
    }
    for (const name of [
      "pmal_claim_host_job", "pmal_complete_generation_job",
      "pmal_complete_dissection_job", "pmal_fail_host_job",
    ]) {
      expect(catalog.find((tool) => tool.name === name)?.definition._meta)
        .toMatchObject({ ui: { visibility: ["model"] } });
    }
    expect(catalog.every((tool) => tool.definition.outputSchema)).toBe(true);
  });

  it("renderiza somente snapshot validado sem consultar o núcleo", async () => {
    let calls = 0;
    const catalog = createToolCatalog({
      call: async () => { calls += 1; return { ok: true, data: {} }; },
    });
    const render = catalog.find((tool) => tool.name === "pmal_render_dashboard");
    const data = { attempts_total: 0, correct_total: 0, accuracy: 0,
      review_total: 0, reviews_due: 0, mastery_by_discipline: {},
      discipline_metrics: [
        "direito_penal_militar", "direito_processual_penal_militar",
        "legislacao_pmal", "conhecimentos_alagoas",
      ].map((discipline) => ({ discipline, mastery: 0, attempts: 0,
        correct: 0, topics_total: 1, topics_covered: 0 })),
      error_patterns: [], syllabus: { topics_total: 4, topics_covered: 0 },
      study_bank: 23,
      concepts: { tracked: 0, mastered: 0, reviews_due: 0 },
      concept_metrics: [],
      corpus: { documents: 43, total_pages: 2071, usable_pages: 2013,
        ocr_pages: 57, quarantined_pages: 1, stale_sources: 0 },
      questions_applied: { official: 0, generated: 0 },
      exemplars: { approved: 0, records: [] }, exemplar_candidates: [],
      confidence: { average: 0, high_confidence_errors: 0 } };

    const result = await render?.handler(data);

    expect(calls).toBe(0);
    expect(result?.structuredContent).toEqual(data);
  });

  it("remove ferramentas exclusivas da interface no fallback textual", () => {
    const catalog = createToolCatalog(
      { call: async () => ({ ok: true, data: {} }) },
      { includeRender: false },
    );

    expect(catalog).toHaveLength(22);
    expect(catalog.map((tool) => tool.name)).not.toContain("pmal_render_dashboard");
    expect(catalog.map((tool) => tool.name)).not.toContain("pmal_open_study_panel");
    expect(catalog.map((tool) => tool.name)).not.toContain("pmal_start_study_session");
    expect(catalog.map((tool) => tool.name)).toContain("pmal_submit_answer");
  });

  it("encaminha trabalhos opacos entre a interface e o modelo", async () => {
    const calls: Array<[string, Record<string, unknown>]> = [];
    const catalog = createToolCatalog({
      call: async (command, input) => {
        calls.push([command, input]);
        if (command === "request-dissection-job") {
          return { ok: true, data: { request_id: "request-1", kind: "dissection", status: "pending" } };
        }
        return { ok: true, data: {
          request_id: "request-1", kind: "dissection", status: "completed",
          result: { exchange_id: "request-1", attempt_id: "attempt-1", sequence: 1,
            user_message: "Explique", assistant_message: "Resposta técnica", model: "codex",
            created_at: "2026-09-07T12:00:00+00:00" },
        } };
      },
    });

    await catalog.find((tool) => tool.name === "pmal_request_dissection_job")?.handler({
      request_id: "request-1", attempt_id: "attempt-1", user_message: "Explique",
    });
    const status = await catalog.find((tool) => tool.name === "pmal_get_host_job_status")?.handler({
      request_id: "request-1",
    });

    expect(calls).toEqual([
      ["request-dissection-job", { request_id: "request-1", attempt_id: "attempt-1", user_message: "Explique" }],
      ["get-host-job-status", { request_id: "request-1" }],
    ]);
    expect(status?.structuredContent).not.toHaveProperty("grading");
    expect(status?.structuredContent).not.toHaveProperty("generation_brief");
  });

  it("devolve conteúdo legível e structuredContent completo", async () => {
    const catalog = createToolCatalog({
      call: async () => ({
        ok: true,
        data: { attempts_total: 2, correct_total: 1, accuracy: 0.5,
          review_total: 1, reviews_due: 0, mastery_by_discipline: {},
          discipline_metrics: [
            "direito_penal_militar",
            "direito_processual_penal_militar",
            "legislacao_pmal",
            "conhecimentos_alagoas",
          ].map((discipline) => ({ discipline, mastery: 0, attempts: 0,
            correct: 0, topics_total: 1, topics_covered: 0 })),
          error_patterns: [], syllabus: { topics_total: 4, topics_covered: 0 },
          study_bank: 23,
          concepts: { tracked: 0, mastered: 0, reviews_due: 0 },
          concept_metrics: [],
          corpus: { documents: 43, total_pages: 2071, usable_pages: 2013,
            ocr_pages: 57, quarantined_pages: 1, stale_sources: 0 },
          questions_applied: { official: 0, generated: 0 },
          exemplars: { approved: 0, records: [] }, exemplar_candidates: [],
          confidence: { average: 0, high_confidence_errors: 0 } },
      }),
    });
    const dashboard = catalog.find((tool) => tool.name === "pmal_get_dashboard");

    const result = await dashboard?.handler({});

    expect(result?.content[0]).toEqual({
      type: "text",
      text: "Painel atualizado: 2 respostas e 0 revisões vencidas.",
    });
    expect(result?.structuredContent).toMatchObject({ attempts_total: 2, accuracy: 0.5 });
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
    expect(result?.content[0]).toEqual({ type: "text", text: "session_not_found: Sessão não localizada." });
    expect(result).not.toHaveProperty("structuredContent");
  });
});
