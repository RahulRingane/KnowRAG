import { z } from "zod";

/**
 * Mirrors `IngestAccepted` / `IngestStatus` from `src/types/api.ts`, plus
 * `DocumentsResponse` for `GET /documents` (see the caveat on that type in
 * `types/api.ts` — its exact shape isn't part of the verified §5.2 contract).
 */

export const DocumentStatusValueSchema = z.enum(["pending", "indexed", "failed"]);

/** `status` is intentionally a bare `z.string()`, not the terminal-state
 *  enum: `202 {..., status: "pending"}` is the only value `POST /ingest`
 *  is documented to return, but pinning it to `DocumentStatusValueSchema`
 *  would make this schema reject a legitimate accept response if the
 *  backend ever returns a synonym here. */
export const IngestAcceptedSchema = z.object({
  document_id: z.number(),
  filename: z.string(),
  status: z.string(),
});

export const IngestStatusSchema = z.object({
  document_id: z.number(),
  filename: z.string(),
  status: DocumentStatusValueSchema,
  chunk_count: z.number(),
  ingested_at: z.string().nullable(),
  error: z.string().nullable(),
});

/** See the doc comment on `DocumentsResponse` in `types/api.ts`: modeled as
 *  a bare array of `IngestStatus`-shaped rows, to confirm in WS-J. */
export const DocumentsResponseSchema = z.array(IngestStatusSchema);
