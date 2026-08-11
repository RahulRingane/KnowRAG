import type { ClaimVerdict } from "@/types/api";

/**
 * Resolves a citation TAG (`"C1"`) to a canonical chunk KEY (`"1:3"`).
 * These are two different vocabularies (trap #4 of the WS-D brief) and this
 * module is the one place that bridges them, the same way `chunk-key.ts` is
 * the one place a chunk key is split or built and `citations.ts` is the one
 * place `[C1]` tags are parsed out of `answer`.
 */

/**
 * Resolves an `answer`-level citation tag against `retrieved_chunk_ids`.
 *
 * There is no field on `FactCheckedResponse` that maps a bare tag to a
 * chunk id directly — that mapping only exists per-claim, as the positional
 * correspondence between `ClaimVerdict.citations` and `.chunk_ids`. But the
 * backend assigns tags purely positionally over the exact same retrieval
 * list that becomes `retrieved_chunk_ids`, in the same order (confirmed
 * against source: `domain/context.py::format_context` numbers `chunks[0]`
 * as `C1`, `chunks[1]` as `C2`, ...; `query_service.py` builds
 * `retrieved_chunk_ids` from that identical `chunks` list right after
 * calling `format_context` on it). So `Cn` is reliably
 * `retrieved_chunk_ids[n-1]` for any tag the generator actually used — this
 * is a derived fact about the pipeline, not a documented contract field,
 * which is why it's isolated here instead of assumed inline at every call
 * site.
 *
 * Returns `null` (never throws, never indexes past the array) for a tag
 * number that doesn't resolve — malformed tag, or one beyond how many
 * chunks were actually retrieved — so a citation chip degrades to inert
 * instead of crashing or silently pointing at the wrong evidence.
 */
export function resolveAnswerCitation(tag: string, retrievedChunkIds: string[]): string | null {
  const match = /^C(\d+)$/.exec(tag);
  if (!match) return null;
  const digits = match[1];
  if (digits === undefined) return null;

  const index = Number(digits) - 1;
  if (!Number.isInteger(index) || index < 0) return null;

  return retrievedChunkIds[index] ?? null;
}

export interface ResolvedCitation {
  tag: string;
  chunkId: string;
}

/**
 * Zips a claim's `citations` (tags) against its `chunk_ids` (canonical
 * keys). Per trap #4, these correspond POSITIONALLY only after the backend
 * has already dropped any tag it couldn't resolve
 * (`domain/verification.py:403`) — so the two arrays are expected to be the
 * same length, but "expected" is not "guaranteed" from the frontend's
 * vantage point, and `noUncheckedIndexedAccess` is exactly the guard rail
 * for not trusting that blindly. Iterates only to the SHORTER length rather
 * than either array's own length, so a drifted pair degrades to "fewer
 * resolvable chips" instead of an out-of-bounds `undefined` reaching a
 * chip's props.
 */
export function zipClaimCitations(claim: Pick<ClaimVerdict, "citations" | "chunk_ids">): ResolvedCitation[] {
  const length = Math.min(claim.citations.length, claim.chunk_ids.length);
  const resolved: ResolvedCitation[] = [];
  for (let i = 0; i < length; i++) {
    const tag = claim.citations[i];
    const chunkId = claim.chunk_ids[i];
    if (tag === undefined || chunkId === undefined) continue;
    resolved.push({ tag, chunkId });
  }
  return resolved;
}
