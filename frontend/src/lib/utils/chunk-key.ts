/**
 * `chunk_ids` on the wire are always the string `"<documentId>:<chunkIndex>"`
 * (backend's `chunk_key()`, CLAUDE.md "Conventions"). This module is the
 * ONLY place that string is split or built on the frontend — every other
 * module that needs a document id + chunk index pair, or needs to build a
 * key to look one up, imports these two functions rather than reaching for
 * `.split(":")` itself. Two independent parsers would eventually diverge
 * on an edge case (e.g. what a malformed key does) exactly the way the
 * backend's own docs warn against for its citation-tag `tag_map`.
 */

export interface ChunkKey {
  documentId: number;
  chunkIndex: number;
}

/** Builds the canonical `"documentId:chunkIndex"` string. */
export function formatChunkKey(documentId: number, chunkIndex: number): string {
  return `${documentId}:${chunkIndex}`;
}

/**
 * Parses a canonical chunk key. Returns `null` — never throws — on
 * anything malformed (wrong separator, non-integer parts, extra
 * whitespace, empty string), so a corrupt or unexpected key coming off the
 * wire degrades to "no chunk resolved" in a render instead of crashing the
 * component tree.
 */
export function parseChunkKey(key: string): ChunkKey | null {
  const match = /^(\d+):(\d+)$/.exec(key.trim());
  if (!match) return null;

  const documentIdStr = match[1];
  const chunkIndexStr = match[2];
  if (documentIdStr === undefined || chunkIndexStr === undefined) return null;

  const documentId = Number(documentIdStr);
  const chunkIndex = Number(chunkIndexStr);
  if (!Number.isSafeInteger(documentId) || !Number.isSafeInteger(chunkIndex)) return null;

  return { documentId, chunkIndex };
}
