import { z } from "zod";

import { FactCheckedResponseSchema } from "./query";

/**
 * Per-event-type payload schemas for `/query/stream`'s SSE frames, plus the
 * discriminated union `StreamEventSchema` mirroring `StreamEvent` in
 * `src/types/api.ts`. `lib/api/sse.ts` validates each decoded frame against
 * the schema matching its `event` name before yielding it, so a malformed
 * or contract-drifted frame throws a caught, descriptive error instead of
 * handing a component `undefined.text`.
 */

export const StreamRetrievalDataSchema = z.object({
  retrieved_chunk_ids: z.array(z.string()),
});

/** RAW JSON FRAGMENT of the claim schema — never prose. See `sse.ts` and
 *  `lib/hooks/useQueryStream.ts`: this must only ever back an activity
 *  indicator, never be rendered as answer text. */
export const StreamTokenDataSchema = z.object({
  text: z.string(),
});

export const StreamVerificationDataSchema = FactCheckedResponseSchema;

/** `Record<string, never>` — an empty payload object, no keys allowed. */
export const StreamDoneDataSchema = z.object({}).strict();

export const StreamErrorDataSchema = z.object({
  detail: z.string(),
});

export const StreamEventSchema = z.discriminatedUnion("event", [
  z.object({ event: z.literal("retrieval"), data: StreamRetrievalDataSchema }),
  z.object({ event: z.literal("token"), data: StreamTokenDataSchema }),
  z.object({ event: z.literal("verification"), data: StreamVerificationDataSchema }),
  z.object({ event: z.literal("done"), data: StreamDoneDataSchema }),
  z.object({ event: z.literal("error"), data: StreamErrorDataSchema }),
]);
