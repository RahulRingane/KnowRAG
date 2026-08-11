/**
 * A client-side PREDICTION of which route the backend will take, mirroring
 * `app/domain/classification.py::classify_input` (`rag/`) closely enough to
 * be useful as a hint. This is NOT the routing decision — the backend
 * classifies independently and authoritatively on every `POST /query` call,
 * and its answer is what `input_type` on the response reports (rendered by
 * `verification/input-type-badge.tsx`). Trap #8 in the WS-D brief: show this
 * as a prediction, never as fact, and never skip rendering the response's
 * own `input_type` because this hint already ran.
 *
 * Ported by hand rather than shared over the wire (there is no "classify"
 * endpoint, and adding one just to avoid a ~15-line port would be a network
 * round trip in front of the very call classification exists to route). Two
 * known divergences from the Python source, both acceptable for a labelled
 * hint:
 *   - Python's `\w` is Unicode-aware; JS's bare `\w` is ASCII-only. A first
 *     word starting with a non-ASCII letter won't match here the way it
 *     would server-side. Harmless for this corpus's English register.
 *   - `first_word`'s leading-noise strip uses the same character class as
 *     `classification.py`'s `_LEADING_NOISE`, transcribed literally.
 */

export type RouteHint = "question" | "fact";

/** Verbatim from `INTERROGATIVE_OPENERS` in `classification.py` — a
 *  deliberately small, closed set (see that module's docstring for why
 *  "are"/"do"/"which" are absent). Keep this in sync by hand if the backend
 *  set ever changes; there is no shared source for it. */
const INTERROGATIVE_OPENERS = new Set([
  "what",
  "why",
  "how",
  "when",
  "where",
  "who",
  "is",
  "does",
  "can",
  "has",
]);

// Transcribed verbatim from classification.py's _LEADING_NOISE.
const LEADING_NOISE = /^[\s'"“”‘’(\[\-–—*>•.]+/;

/** Mirrors `classification.py::first_word`. */
export function firstWord(text: string): string {
  const cleaned = text.trim().replace(LEADING_NOISE, "");
  const match = /^[\w']+/.exec(cleaned);
  return match ? match[0].toLowerCase() : "";
}

/** Mirrors `classification.py::classify_input`, minus the `ClassifiedInput`
 *  wrapper this frontend has no use for — just the two-rule decision. */
export function classifyHint(text: string): RouteHint {
  const stripped = text.trim();
  if (stripped.length === 0) return "fact"; // matches the backend's fall-through default
  if (stripped.endsWith("?")) return "question";
  if (INTERROGATIVE_OPENERS.has(firstWord(stripped))) return "question";
  return "fact";
}
