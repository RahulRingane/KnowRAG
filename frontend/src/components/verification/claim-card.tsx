import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { formatPercent } from "@/lib/utils/format";
import { cn } from "@/lib/utils";
import { getVerdictMeta } from "@/lib/utils/verdict";
import type { ClaimVerdict } from "@/types/api";

import { CitationChip } from "./citation-chip";
import { zipClaimCitations } from "./citation-resolution";

export interface ClaimCardProps {
  claim: ClaimVerdict;
  onOpenChunk: (chunkId: string) => void;
  /**
   * Trap #5: on the fact route, the single claim's `citations`/`chunk_ids`
   * list every chunk the verifier CONSIDERED against the caller's
   * statement, not the subset that supports it — `evidence_score` and
   * `chunk_ids` on the verdict itself carry the actual attribution. Set by
   * the caller (`verification-result.tsx`, from `input_type === "fact"`)
   * so this card can swap the cited-chunks heading instead of implying
   * corroboration that was never established.
   */
  citationsAreExhaustive?: boolean;
}

/**
 * A single claim's verdict. Status icon+label comes from `VERDICT_META`
 * (`lib/utils/verdict.ts`) — the only place a verdict maps to colour or
 * icon — never re-derived here.
 */
export function ClaimCard({ claim, onOpenChunk, citationsAreExhaustive = false }: ClaimCardProps) {
  const meta = getVerdictMeta(claim.status);
  const Icon = meta.icon;
  const cited = zipClaimCitations(claim);

  return (
    <Card className={cn("border", meta.borderClassName)}>
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={cn(
              "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
              meta.bgClassName,
              meta.textClassName,
            )}
          >
            <Icon className="size-3.5" aria-hidden="true" />
            {meta.label}
          </span>
          <span className="text-muted-foreground text-xs">
            {/* REFUSAL claims are never scored (verification.py branch order) —
                render that explicitly rather than letting a null read as 0%. */}
            {claim.evidence_score === null
              ? "not scored"
              : `evidence score ${formatPercent(claim.evidence_score)}`}
          </span>
        </div>
      </CardHeader>
      <CardContent className="space-y-2">
        <p className="text-sm">{claim.text}</p>

        {claim.reason ? <p className="text-muted-foreground text-xs italic">{claim.reason}</p> : null}

        {cited.length > 0 ? (
          <div className="flex flex-wrap items-center gap-1 pt-1">
            <span className="text-muted-foreground mr-1 text-xs">
              {citationsAreExhaustive ? "Chunks considered:" : "Cited:"}
            </span>
            {cited.map(({ tag, chunkId }) => (
              <CitationChip key={tag} tag={tag} chunkId={chunkId} onOpen={onOpenChunk} />
            ))}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
