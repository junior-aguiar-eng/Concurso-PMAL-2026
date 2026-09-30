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
export const EvidenceRefSchema = z.strictObject({
    id: z.string().min(1),
    kind: z.enum(["local", "live"]),
    source: z.string().min(1),
    locator: z.string().min(1),
    excerpt: z.string().min(1),
    sha256: z.string().regex(/^[0-9a-f]{64}$/),
    authority: z.string().min(1),
    page: z.number().int().positive().nullable(),
    url: z.url().nullable(),
    retrieved_at: z.iso.datetime({ offset: true }).nullable(),
    untrusted_text: z.literal(true),
});
export const ExemplarReferenceSchema = z.strictObject({
    question_id: z.string().min(1),
    statement: z.string().min(1),
    construction_pattern: z.string(),
    difficulty: z.number().int().min(1).max(5),
    trap: z.string(),
});
export const GenerationBriefSchema = z.strictObject({
    job_id: z.string().min(1),
    session_id: z.string().min(1),
    discipline: DisciplineSchema,
    topic_id: z.string().min(1),
    concept_key: z.string().min(1),
    difficulty: z.number().int().min(1).max(5),
    pedagogical_reason: z.string().min(1),
    evidence: z.array(EvidenceRefSchema).min(1),
    exemplars: z.array(ExemplarReferenceSchema).max(3),
    update_policy: z.enum(["local_sufficient", "selective_live_confirmation"]),
});
export const PrivatePreparedNextItemSchema = z.strictObject({
    kind: z.enum(["question", "generation"]),
    question: PublicQuestionSchema.nullable(),
    generation_brief: GenerationBriefSchema.nullable(),
});
export const DissectionExchangeSchema = z.strictObject({
    exchange_id: z.string().min(1), attempt_id: z.string().min(1),
    sequence: z.number().int().positive(), user_message: z.string().min(1),
    assistant_message: z.string().min(1), model: z.string().min(1),
    created_at: z.iso.datetime({ offset: true }),
});
export const HostJobReferenceSchema = z.strictObject({
    request_id: z.string().min(1),
    kind: z.enum(["question_generation", "dissection"]),
    status: z.enum(["pending", "processing", "completed", "failed"]),
});
export const HostJobStatusSchema = HostJobReferenceSchema.extend({
    result: z.union([PublicQuestionSchema, DissectionExchangeSchema]).optional(),
    error: z.strictObject({ code: z.string().min(1), message: z.string().min(1) }).optional(),
});
export const PreparedNextItemSchema = z.strictObject({
    kind: z.enum(["question", "generation", "terminal"]),
    question: PublicQuestionSchema.nullable(),
    generation_request: HostJobReferenceSchema.nullable(),
});
export const GeneratedDraftSchema = z.strictObject({
    statement: z.string().min(40), answer: z.enum(["C", "E"]),
    rationale: z.string().min(30), construction_pattern: z.string().min(1),
    difficulty: z.number().int().min(1).max(5), concept: z.string().min(1),
    decisive_expression: z.string().min(1), trap: z.string().min(1),
    distinction: z.string().min(1),
    evidence_links: z.array(z.strictObject({
        evidence_id: z.string().min(1), claim_key: z.string().min(1),
    })).min(1),
});
export const HostJobContextSchema = z.strictObject({
    request_id: z.string().min(1),
    kind: z.enum(["question_generation", "dissection"]),
    generation_brief: GenerationBriefSchema.optional(),
    additional_jobs: z.array(z.strictObject({
        request_id: z.string().min(1),
        generation_brief: GenerationBriefSchema,
    })).optional(),
    attempt_id: z.string().min(1).optional(),
    question: PublicQuestionSchema.optional(),
    grading: z.lazy(() => GradingResultSchema).optional(),
    user_message: z.string().min(1).optional(),
    conversation: z.array(DissectionExchangeSchema).optional(),
});
export const SessionSummarySchema = z.strictObject({
    session_id: z.string().min(1), attempts: z.number().int().nonnegative(),
    correct: z.number().int().nonnegative(), accuracy: z.number().min(0).max(1),
});
export const EvidenceSearchSchema = z.array(EvidenceRefSchema);
export const LiveEvidenceSchema = z.strictObject({
    id: z.string().min(1),
    url: z.url(),
    source_kind: z.enum([
        "planalto", "stf", "stj", "cebraspe", "pmal", "alagoas_governo",
        "aleal", "tjal", "ibge", "ufal", "academic",
    ]),
    author: z.string().nullable(),
    locator: z.string().min(1),
    excerpt: z.string().min(1),
    retrieved_at: z.iso.datetime({ offset: true }),
    sha256: z.string().regex(/^[0-9a-f]{64}$/),
    authority: z.string().min(1),
    status: z.enum(["verified", "changed"]),
    previous_snapshot_id: z.string().nullable(),
    untrusted_text: z.literal(true),
});
export const CorpusRefreshSchema = z.strictObject({
    documents_found: z.number().int().nonnegative(),
    documents_processed: z.number().int().nonnegative(),
    documents_unchanged: z.number().int().nonnegative(),
    documents_altered: z.number().int().nonnegative(),
    total_pages: z.number().int().nonnegative(),
    pages_processed: z.number().int().nonnegative(),
    usable_pages: z.number().int().nonnegative(),
    ocr_pages: z.number().int().nonnegative(),
    empty_pages: z.number().int().nonnegative(),
    quarantined_pages: z.number().int().nonnegative(),
    failed_pages: z.number().int().nonnegative(),
    chunks_created: z.number().int().nonnegative(),
});
export const ExemplarRecordSchema = z.strictObject({
    question_id: z.string().min(1),
    state: z.enum(["approved", "revoked"]),
    approved_at: z.iso.datetime({ offset: true }),
    revoked_at: z.iso.datetime({ offset: true }).nullable(),
    event_actor: z.string().min(1),
    reason: z.string().nullable(),
});
export const GradingResultSchema = z.strictObject({
    correct: z.boolean(),
    expected_answer: z.enum(["C", "E"]),
    rationale: z.string().min(1),
    source_label: z.string().min(1),
    source_page: z.number().int().nonnegative(),
    next_review_at: z.iso.datetime({ offset: true }),
    error_pattern: z.string().nullable(),
    analysis: z.string().min(1),
    decisive_expression: z.string().min(1),
    trap: z.string().min(1),
    distinction: z.string().min(1),
    evidence: z.array(EvidenceRefSchema),
});
export const DashboardSchema = z.strictObject({
    attempts_total: z.number().int().nonnegative(),
    correct_total: z.number().int().nonnegative(),
    accuracy: z.number().min(0).max(1),
    review_total: z.number().int().nonnegative(),
    reviews_due: z.number().int().nonnegative(),
    mastery_by_discipline: z.record(z.string(), z.number().min(0).max(1)),
    discipline_metrics: z.array(z.strictObject({
        discipline: DisciplineSchema,
        mastery: z.number().min(0).max(1),
        attempts: z.number().int().nonnegative(),
        correct: z.number().int().nonnegative(),
        topics_total: z.number().int().nonnegative(),
        topics_covered: z.number().int().nonnegative(),
    })).length(4),
    error_patterns: z.array(z.strictObject({
        pattern: z.string().min(1),
        count: z.number().int().positive(),
    })),
    syllabus: z.strictObject({
        topics_total: z.number().int().nonnegative(),
        topics_covered: z.number().int().nonnegative(),
    }),
    study_bank: z.number().int().nonnegative(),
    concepts: z.strictObject({
        tracked: z.number().int().nonnegative(),
        mastered: z.number().int().nonnegative(),
        reviews_due: z.number().int().nonnegative(),
    }),
    concept_metrics: z.array(z.strictObject({
        learning_target_id: z.string().min(1),
        discipline: DisciplineSchema,
        topic_id: z.string().min(1),
        concept_key: z.string().min(1),
        mastery: z.number().min(0).max(1),
        next_review_at: z.iso.datetime({ offset: true }),
        due: z.boolean(),
    })),
    corpus: z.strictObject({
        documents: z.number().int().nonnegative(),
        total_pages: z.number().int().nonnegative(),
        usable_pages: z.number().int().nonnegative(),
        ocr_pages: z.number().int().nonnegative(),
        quarantined_pages: z.number().int().nonnegative(),
        stale_sources: z.number().int().nonnegative(),
    }),
    questions_applied: z.strictObject({
        official: z.number().int().nonnegative(),
        generated: z.number().int().nonnegative(),
    }),
    exemplars: z.strictObject({
        approved: z.number().int().nonnegative(),
        records: z.array(z.strictObject({
            question_id: z.string().min(1), state: z.literal("approved"),
            discipline: DisciplineSchema, topic_id: z.string().min(1),
            approved_at: z.iso.datetime({ offset: true }),
        })),
    }),
    exemplar_candidates: z.array(z.strictObject({
        question_id: z.string().min(1), discipline: DisciplineSchema,
        topic_id: z.string().min(1), statement: z.string().min(1),
    })),
    confidence: z.strictObject({
        average: z.number().min(0).max(3),
        high_confidence_errors: z.number().int().nonnegative(),
    }),
});
export const StudyWorkspaceSchema = z.strictObject({
    phase: z.enum(["configuration", "question", "generation", "correction", "dissection", "completed"]),
    session: SessionSchema.nullable(),
    question: PublicQuestionSchema.nullable(),
    generation_request: HostJobReferenceSchema.nullable().optional(),
    attempt_id: z.string().min(1).nullable().optional(),
    grading: GradingResultSchema.nullable().optional(),
    next_item: z.strictObject({
        kind: z.enum(["question", "generation", "terminal"]), question: PublicQuestionSchema.nullable(),
        generation_request: HostJobReferenceSchema.nullable(),
    }).nullable().optional(),
    conversation: z.array(DissectionExchangeSchema),
    dashboard: DashboardSchema,
});
export const SubmitAndPrepareSchema = z.strictObject({
    attempt_id: z.string().min(1),
    grading: GradingResultSchema,
    session_summary: SessionSummarySchema,
    metric_deltas: z.strictObject({
        attempts: z.number().int(), correct: z.number().int(), reviews_due: z.number().int().nonnegative(),
    }),
    next_item: z.strictObject({
        kind: z.enum(["question", "generation", "terminal"]),
        question: PublicQuestionSchema.nullable(),
        generation_request: HostJobReferenceSchema.nullable(),
    }),
});
export const EndSessionSchema = SessionSummarySchema.extend({
    status: z.literal("completed"), ended_at: z.iso.datetime({ offset: true }),
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
    source_kind: LiveEvidenceSchema.shape.source_kind,
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
