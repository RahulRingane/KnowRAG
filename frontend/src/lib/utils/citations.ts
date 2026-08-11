/**
 * Splits a `FactCheckedResponse.answer` string carrying literal `[C1][C3]`
 * citation tags (backend `domain/assembly.py`) into an ordered list of
 * plain-text and citation segments, so WS-D can render the tags as
 * interactive chips instead of literal bracketed text.
 */

export type CitationSegment =
  | { type: "text"; text: string }
  | { type: "citation"; tag: string; index: number };

const CITATION_TAG_RE = /\[C(\d+)\]/g;

/**
 * Parses `answer` into segments in reading order. Handles:
 * - **no tags** — returns a single `text` segment (the whole string).
 * - **adjacent tags** (`[C1][C2]`) — two `citation` segments back to
 *   back, no empty `text` segment between them.
 * - **a tag mid-sentence** — `text` segments on both sides.
 * - **malformed tags** (`[C]`, `[Cx]`, `[c1]`) — the regex requires a `C`
 *   followed by one or more digits, so anything that doesn't match is
 *   left as ordinary `text` rather than silently dropped or half-parsed.
 */
export function parseCitations(answer: string): CitationSegment[] {
  const segments: CitationSegment[] = [];
  let lastIndex = 0;

  for (const match of answer.matchAll(CITATION_TAG_RE)) {
    const matchIndex = match.index;
    const digits = match[1];
    // `RegExpMatchArray.index`/group captures are typed optional in TS's
    // lib even though `matchAll` always populates both for a global regex
    // with a capture group; guard rather than assert, for
    // `noUncheckedIndexedAccess` and general defensiveness.
    if (matchIndex === undefined || digits === undefined) continue;

    if (matchIndex > lastIndex) {
      segments.push({ type: "text", text: answer.slice(lastIndex, matchIndex) });
    }
    segments.push({ type: "citation", tag: `C${digits}`, index: Number(digits) });
    lastIndex = matchIndex + match[0].length;
  }

  if (lastIndex < answer.length) {
    segments.push({ type: "text", text: answer.slice(lastIndex) });
  }

  return segments;
}

/** Unique citation tags referenced in `answer`, in first-appearance order
 *  — useful for cross-referencing against a `ClaimVerdict.citations` list
 *  without re-deriving the regex at each call site. */
export function getUniqueCitationTags(answer: string): string[] {
  const seen = new Set<string>();
  for (const segment of parseCitations(answer)) {
    if (segment.type === "citation") seen.add(segment.tag);
  }
  return [...seen];
}
