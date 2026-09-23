import { z } from "zod";
export const DisciplineSchema = z.enum([
    "direito_penal_militar",
    "direito_processual_penal_militar",
    "legislacao_pmal",
    "conhecimentos_alagoas",
]);
export const SessionModeSchema = z.enum([
    "diagnostic",
    "timed",
    "discipline",
    "review",
    "mixed_mock",
]);
export const SessionSchema = z.strictObject({
    session_id: z.string().min(1),
    mode: SessionModeSchema,
    duration_minutes: z.number().int().positive().nullable(),
    disciplines: z.array(DisciplineSchema),
    started_at: z.iso.datetime({ offset: true }),
    status: z.literal("open"),
});
export const PublicQuestionSchema = z.strictObject({
    id: z.string().min(1),
    discipline: DisciplineSchema,
    topic_id: z.string().min(1),
    statement: z.string().min(1),
    origin_label: z.enum(["oficial", "adaptada", "inédita"]),
});
export const GradingResultSchema = z.strictObject({
    correct: z.boolean(),
    expected_answer: z.enum(["C", "E"]),
    rationale: z.string().min(1),
    source_label: z.string().min(1),
    source_page: z.number().int().nonnegative(),
    next_review_at: z.iso.datetime({ offset: true }),
    error_pattern: z.string().nullable(),
});
export const DashboardSchema = z.strictObject({
    attempts_total: z.number().int().nonnegative(),
    correct_total: z.number().int().nonnegative(),
    accuracy: z.number().min(0).max(1),
    review_total: z.number().int().nonnegative(),
    reviews_due: z.number().int().nonnegative(),
    mastery_by_discipline: z.record(z.string(), z.number().min(0).max(1)),
    study_bank: z.number().int().nonnegative(),
});
export const ReviewQueueSchema = z.array(z.strictObject({
    question_id: z.string().min(1),
    discipline: DisciplineSchema,
    topic_id: z.string().min(1),
    next_review_at: z.iso.datetime({ offset: true }),
    interval_days: z.number().int().min(1).max(60),
    mastery: z.number().min(0).max(1),
    due: z.boolean(),
}));
export const CanvasExportSchema = z.strictObject({ markdown: z.string() });
export const SourceCheckSchema = z.strictObject({
    id: z.string().min(1),
    url: z.url(),
    source_kind: z.enum(["planalto", "stf", "stj"]),
    retrieved_at: z.iso.datetime({ offset: true }),
    sha256: z.string().regex(/^[0-9a-f]{64}$/),
    status: z.enum(["verified", "changed"]),
    citation: z.string().min(1),
    bytes_read: z.number().int().nonnegative(),
});
export const CliErrorSchema = z.strictObject({
    code: z.string().min(1),
    message: z.string().min(1),
});
export const CliEnvelopeSchema = z.discriminatedUnion("ok", [
    z.strictObject({ ok: z.literal(true), data: z.unknown() }),
    z.strictObject({ ok: z.literal(false), error: CliErrorSchema }),
]);
