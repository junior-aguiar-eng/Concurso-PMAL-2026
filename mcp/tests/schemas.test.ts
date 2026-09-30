import { describe, expect, it } from "vitest";

import { GenerationBriefSchema, HostJobContextSchema, PublicQuestionSchema } from "../src/schemas.js";

describe("PublicQuestionSchema", () => {
  it("rejeita qualquer gabarito na questão pública", () => {
    const parsed = PublicQuestionSchema.safeParse({
      id: "q1",
      discipline: "legislacao_pmal",
      topic_id: "leg.1",
      statement: "texto",
      origin_label: "inédita",
      answer: "C",
    });

    expect(parsed.success).toBe(false);
  });
});

describe("GenerationBriefSchema", () => {
  it("exige que evidências recuperadas sejam marcadas como texto não confiável", () => {
    const parsed = GenerationBriefSchema.safeParse({
      job_id: "j1", session_id: "s1", discipline: "direito_penal_militar",
      topic_id: "dpm.1", concept_key: "legacy:dpm.1", difficulty: 4,
      pedagogical_reason: "conceito não avaliado", exemplars: [],
      update_policy: "local_sufficient",
      evidence: [{ id: "e1", kind: "local", source: "CPM.pdf", locator: "Art. 1º",
        excerpt: "texto", sha256: "a".repeat(64), authority: "official_legislation",
        page: 1, url: null, retrieved_at: null, untrusted_text: false }],
    });

    expect(parsed.success).toBe(false);
  });
});

describe("HostJobContextSchema", () => {
  const brief = (jobId: string) => ({
    job_id: jobId, session_id: "s1", discipline: "legislacao_pmal",
    topic_id: "leg.1", concept_key: "legacy:leg.1", difficulty: 4,
    pedagogical_reason: "conceito não avaliado", exemplars: [],
    update_policy: "local_sufficient",
    evidence: [{ id: "e1", kind: "local", source: "L.pdf", locator: "p. 1",
      excerpt: "texto", sha256: "a".repeat(64), authority: "local_official_copy",
      page: 1, url: null, retrieved_at: null, untrusted_text: true }],
  });

  it("aceita os demais trabalhos do lote no mesmo contexto", () => {
    const parsed = HostJobContextSchema.safeParse({
      request_id: "r1", kind: "question_generation", generation_brief: brief("j1"),
      additional_jobs: [{ request_id: "r2", generation_brief: brief("j2") }],
    });

    expect(parsed.success).toBe(true);
  });
});
