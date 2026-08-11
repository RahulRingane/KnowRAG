import { CheckCircle2, Loader2 } from "lucide-react";

import type { QueryStreamPhase } from "@/lib/hooks/useQueryStream";
import type { StreamEvent } from "@/types/api";

/**
 * The live activity indicator for `/query/stream`. Trap #7 of the WS-D
 * brief, the single most important correctness rule in this workstream:
 * `token` events carry raw JSON fragments of the claim schema being
 * generated, NOT prose, and nothing before the terminal `verification`
 * event has been fact-checked. This component is deliberately incapable of
 * leaking one onto the screen — it never reads `.data.text` off a `token`
 * event, only counts how many have arrived, so there is no code path here
 * that could regress into rendering generation output as an answer.
 *
 * `aria-live="polite"` per §5.3: phase changes announce to a screen reader
 * without interrupting whatever it's currently reading.
 */
export function StreamingActivity({
  phase,
  events,
  retrievedChunkIds,
  hasResult,
}: {
  phase: QueryStreamPhase;
  events: StreamEvent[];
  retrievedChunkIds: string[] | null;
  /** True once a `verification` event has landed — kept separate from
   *  `phase` because `phase` can still read `"streaming"` for the brief gap
   *  between `verification` and the terminal `done` event, and by then the
   *  result is already renderable; this suppresses the "generating…" copy
   *  during that gap instead of it flashing after the answer appears. */
  hasResult: boolean;
}) {
  if (phase === "idle" || phase === "error") return null;

  const tokenCount = events.filter((e) => e.event === "token").length;

  let message: string;
  if (phase === "connecting") {
    message = "Connecting…";
  } else if (phase === "done" || hasResult) {
    message = "Verification complete.";
  } else if (retrievedChunkIds) {
    // Evidence retrieved, generation in progress. Zero `token` events is
    // expected and correct on the fact route (trap #7 / plan §1.2) — that
    // branch never claims "generating" for a call that generates nothing.
    message =
      tokenCount > 0
        ? `Retrieved ${retrievedChunkIds.length} evidence chunk${retrievedChunkIds.length === 1 ? "" : "s"} — generating (${tokenCount} fragment${tokenCount === 1 ? "" : "s"} so far)…`
        : `Retrieved ${retrievedChunkIds.length} evidence chunk${retrievedChunkIds.length === 1 ? "" : "s"} — verifying…`;
  } else {
    message = "Retrieving evidence…";
  }

  return (
    <div
      role="status"
      aria-live="polite"
      className="text-muted-foreground flex items-center gap-2 text-sm"
    >
      {phase === "done" || hasResult ? (
        <CheckCircle2 className="text-verdict-supported size-4" aria-hidden="true" />
      ) : (
        <Loader2 className="size-4 animate-spin" aria-hidden="true" />
      )}
      <span>{message}</span>
    </div>
  );
}
