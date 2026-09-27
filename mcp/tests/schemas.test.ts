import { describe, expect, it } from "vitest";

import { GenerationBriefSchema, PublicQuestionSchema } from "../src/schemas.js";

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
