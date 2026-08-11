import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import type { FactCheckedResponse } from "@/types/api";

import { AnswerRenderer } from "./answer-renderer";
import { ClaimsPanel } from "./claims-panel";
import { InputTypeBadge } from "./input-type-badge";
import { LatencyBreakdown } from "./latency-breakdown";
import { StateBanner } from "./state-banner";

/**
 * The full result surface for a completed `FactCheckedResponse`, on either
 * route. Deliberately takes a non-null `response` and renders unconditionally
 * — the idle/loading/error/empty split (§5.3) is the caller's job
 * (`query-workspace.tsx`), which only mounts this once a response exists,
 * the same way `EvidencePanel` independently owns its own four states for
 * its own `useChunks` call.
 */
export function VerificationResult({
  response,
  onOpenChunk,
}: {
  response: FactCheckedResponse;
  onOpenChunk: (chunkId: string) => void;
}) {
  return (
    <div className="space-y-4" aria-live="polite">
      <div className="flex flex-wrap items-center gap-2">
        <InputTypeBadge inputType={response.input_type} />
      </div>

      <StateBanner state={response.state} />

      <Card>
        <CardHeader>
          <h2 className="text-sm font-medium">Answer</h2>
        </CardHeader>
        <CardContent>
          <AnswerRenderer
            answer={response.answer}
            retrievedChunkIds={response.retrieved_chunk_ids}
            onOpenChunk={onOpenChunk}
          />
        </CardContent>
      </Card>

      <ClaimsPanel
        claims={response.claims}
        rejectedClaims={response.rejected_claims}
        refusals={response.refusals}
        inputType={response.input_type}
        onOpenChunk={onOpenChunk}
      />

      <Separator />

      <LatencyBreakdown latencyMs={response.latency_ms} />
    </div>
  );
}
