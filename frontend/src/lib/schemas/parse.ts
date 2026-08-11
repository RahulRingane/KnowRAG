import type { ZodType } from "zod";

/**
 * Parses `data` against `schema`, throwing a descriptive `Error` on
 * mismatch rather than returning a Zod result object. Every `lib/api/
 * endpoints.ts` function calls this immediately after `apiRequest()`
 * resolves — that's "the boundary": once a response has been parsed here,
 * every layer above it (hooks, components) can trust the shape matches
 * `types/api.ts` exactly, with no further `unknown`/optional-chaining
 * needed to guard against backend drift.
 */
export function parseOrThrow<T>(schema: ZodType<T>, data: unknown, context: string): T {
  const result = schema.safeParse(data);
  if (!result.success) {
    throw new Error(`[${context}] Response failed schema validation: ${result.error.message}`);
  }
  return result.data;
}
