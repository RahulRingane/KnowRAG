import { getVerdictMeta, type VerdictStatus } from "@/lib/utils/verdict";
import { cn } from "@/lib/utils";
import type { QueryState } from "@/types/api";

/**
 * Maps a stored `HistoryEntry.state` (`QueryState`) to the closest
 * `VerdictStatus` so its colour/icon comes from `lib/utils/verdict.ts` —
 * the one place a verdict-ish status is mapped to a colour (§5.3: "never
 * hardcode a verdict colour"). `QueryState` and `ClaimStatus` are
 * different wire types (a response has one `state` but many per-claim
 * `status` values) so this is a deliberate, narrow reinterpretation for
 * display, not a claim that they're the same thing: "ok" reads as
 * evidence having supported *something* in the answer, "insufficient
 * evidence" as nothing being confirmed, "contradicted" as the fact route's
 * own contradiction outcome — REFUSAL has no `QueryState` counterpart and
 * is intentionally absent from this map.
 */
const STATE_TO_VERDICT: Record<QueryState, VerdictStatus> = {
  ok: "SUPPORTED",
  insufficient_evidence: "UNSUPPORTED",
  contradicted: "CONTRADICTED",
};

const STATE_LABEL: Record<QueryState, string> = {
  ok: "Answered",
  insufficient_evidence: "Insufficient evidence",
  contradicted: "Contradicted",
};

export function HistoryStateBadge({ state }: { state: QueryState }) {
  const meta = getVerdictMeta(STATE_TO_VERDICT[state]);
  const Icon = meta.icon;
  return (
    <span className={cn("inline-flex items-center gap-1 text-xs font-medium", meta.textClassName)}>
      <Icon className="size-3.5" aria-hidden="true" />
      {STATE_LABEL[state]}
    </span>
  );
}
