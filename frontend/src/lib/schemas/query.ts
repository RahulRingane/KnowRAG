import { z } from "zod";

/**
 * Mirrors `FactCheckedResponse` and friends from `src/types/api.ts`. Parsed
 * at the API boundary (`lib/api/endpoints.ts`) so a backend contract drift
 * — a renamed field, a status value the frontend doesn't know about —
 * surfaces as one caught, loggable error instead of `undefined` silently
 * propagating into a render and producing a blank panel somewhere else.
 *
 * `latency_ms` is `z.record(z.string(), z.number())`, not a fixed object:
 * the fact route emits no `generation_ms` key at all (CLAUDE.md, "Latency
 * and the timing breakdown"), and future stages will add keys this schema
 * must not reject.
 */

export const InputTypeSchema = z.enum(["question", "fact"]);
export const QueryStateSchema = z.enum(["ok", "insufficient_evidence", "contradicted"]);
export const ClaimStatusSchema = z.enum(["SUPPORTED", "UNSUPPORTED", "CONTRADICTED", "REFUSAL"]);

export const ClaimVerdictSchema = z.object({
  text: z.string(),
  status: ClaimStatusSchema,
  citations: z.array(z.string()),
  evidence_score: z.number().nullable(),
  chunk_ids: z.array(z.string()),
  reason: z.string().nullable(),
});

export const FactCheckedResponseSchema = z.object({
  input_type: InputTypeSchema,
  question: z.string(),
  answer: z.string(),
  state: QueryStateSchema,
  claims: z.array(ClaimVerdictSchema),
  retrieved_chunk_ids: z.array(z.string()),
  latency_ms: z.record(z.string(), z.number()),
  rejected_claims: z.array(ClaimVerdictSchema),
  refusals: z.array(ClaimVerdictSchema),
});

/** Request body for `POST /query` — named `question` even for a statement
 *  routed to the fact-check path (backend classifies from the text itself). */
export const QueryRequestSchema = z.object({
  question: z.string().min(1),
});
