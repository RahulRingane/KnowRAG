import { z } from "zod";

/**
 * Mirrors `ChunkText` from `src/types/api.ts`, plus `ChunksResponse` for
 * `GET /chunks` (see the caveat on that type in `types/api.ts` — its exact
 * shape isn't part of the verified §5.2 contract, only its behaviour is).
 */

export const ChunkTextSchema = z.object({
  document_id: z.number(),
  chunk_index: z.number(),
  text: z.string(),
});

export const ChunksResponseSchema = z.array(ChunkTextSchema);
