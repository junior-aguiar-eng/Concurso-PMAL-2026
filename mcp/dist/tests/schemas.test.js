import { describe, expect, it } from "vitest";
import { PublicQuestionSchema } from "../src/schemas.js";
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
