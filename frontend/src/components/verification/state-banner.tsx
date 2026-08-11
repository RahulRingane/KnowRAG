import type { LucideIcon } from "lucide-react";
import { AlertTriangle, CheckCircle2, HelpCircle } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { cn } from "@/lib/utils";
import type { QueryState } from "@/types/api";

/**
 * Trap #1 of the WS-D brief, and the single easiest way to invert this
 * product's meaning: `state: "insufficient_evidence"` is a normal `200`,
 * not an error (`routes/query.py:57`). If this banner ever renders that
 * state with the same visual treatment as a failed request, the product's
 * whole point — "tell the caller what the evidence does and doesn't
 * establish, don't hide the gap" — is undone. All three values render here
 * with their own icon AND their own text; none of them is styled as "this
 * broke."
 *
 * `QueryState` ("ok" | "insufficient_evidence" | "contradicted") is a
 * different vocabulary from `ClaimStatus` ("SUPPORTED" | "UNSUPPORTED" |
 * "CONTRADICTED" | "REFUSAL") — `lib/utils/verdict.ts` explicitly owns only
 * the latter mapping, so this file does NOT import `VERDICT_META` or
 * reassign its colours to a different type. It borrows the same verdict
 * COLOUR TOKENS directly (`--color-verdict-*`, defined once in
 * globals.css) where the underlying meaning is genuinely the same
 * relationship to evidence — "insufficient_evidence" is the query-level
 * echo of what an UNSUPPORTED claim means (a normal, non-failure gap,
 * amber not red — see `verdict.ts`'s own description text), and
 * "contradicted" here IS a CONTRADICTED verdict, just surfaced at the
 * whole-response level for the fact route. "ok" has no verdict analogue
 * (an `ok` answer can still contain UNSUPPORTED claims per trap #2), so it
 * gets its own neutral treatment rather than being force-mapped onto
 * SUPPORTED.
 */

interface StateMeta {
  label: string;
  description: string;
  icon: LucideIcon;
  textClassName: string;
  bgClassName: string;
  borderClassName: string;
}

const STATE_META: Record<QueryState, StateMeta> = {
  ok: {
    label: "Answer generated",
    description: "The evidence was sufficient to produce an answer below.",
    icon: CheckCircle2,
    textClassName: "text-foreground",
    bgClassName: "bg-muted/50",
    borderClassName: "border-border",
  },
  insufficient_evidence: {
    label: "Insufficient evidence",
    description:
      "Nothing answerable could be generated and confirmed from the retrieved evidence. This is a normal outcome for this corpus, not an error.",
    icon: HelpCircle,
    textClassName: "text-verdict-unsupported",
    bgClassName: "bg-verdict-unsupported-bg",
    borderClassName: "border-verdict-unsupported",
  },
  contradicted: {
    label: "Contradicted by evidence",
    description:
      "The retrieved evidence actively disagrees with this statement (fact-check route only).",
    icon: AlertTriangle,
    textClassName: "text-verdict-contradicted",
    bgClassName: "bg-verdict-contradicted-bg",
    borderClassName: "border-verdict-contradicted",
  },
};

export function StateBanner({ state }: { state: QueryState }) {
  const meta = STATE_META[state];
  const Icon = meta.icon;

  return (
    <Alert className={cn(meta.bgClassName, meta.borderClassName)}>
      <Icon className={cn("size-4", meta.textClassName)} aria-hidden="true" />
      <AlertTitle className={meta.textClassName}>{meta.label}</AlertTitle>
      <AlertDescription>{meta.description}</AlertDescription>
    </Alert>
  );
}
