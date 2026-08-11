import { EmptyState } from "@/components/layout/empty-state";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { ClaimVerdict, InputType } from "@/types/api";

import { ClaimCard } from "./claim-card";

export interface ClaimsPanelProps {
  claims: ClaimVerdict[];
  rejectedClaims: ClaimVerdict[];
  refusals: ClaimVerdict[];
  inputType: InputType;
  onOpenChunk: (chunkId: string) => void;
}

/**
 * The three claim views as tabs rather than three stacked lists. `claims`,
 * `rejected_claims`, and `refusals` are server-computed PROJECTIONS of the
 * same underlying list (`rejected_claims` = UNSUPPORTED + CONTRADICTED,
 * `refusals` = REFUSAL) — every rejected or refused claim also appears in
 * `claims`. Stacking all three as separate sections would triple-render a
 * refusal; tabs let each framing exist without that repetition.
 *
 * Trap #2, the framing that matters most here: "Needs scrutiny" does NOT
 * mean "removed from the answer." On the question route `answer` already
 * contains every SUPPORTED and UNSUPPORTED claim — `rejected_claims` means
 * "the evidence couldn't confirm this," which is a normal outcome for a
 * generated paraphrase, not a deletion log. Only CONTRADICTED claims are
 * actually withheld from `answer`, and the copy below says so explicitly
 * rather than leaving "rejected" to imply a uniform fate.
 */
export function ClaimsPanel({
  claims,
  rejectedClaims,
  refusals,
  inputType,
  onOpenChunk,
}: ClaimsPanelProps) {
  const citationsAreExhaustive = inputType === "fact";

  return (
    <Tabs defaultValue="all">
      <TabsList>
        <TabsTrigger value="all">All claims ({claims.length})</TabsTrigger>
        <TabsTrigger value="rejected">Needs scrutiny ({rejectedClaims.length})</TabsTrigger>
        <TabsTrigger value="refusals">Declined ({refusals.length})</TabsTrigger>
      </TabsList>

      <TabsContent value="all" className="space-y-2 pt-2">
        {claims.length === 0 ? (
          <EmptyState title="No claims" description="No claims were generated for this response." />
        ) : (
          claims.map((claim, i) => (
            <ClaimCard
              key={i}
              claim={claim}
              onOpenChunk={onOpenChunk}
              citationsAreExhaustive={citationsAreExhaustive}
            />
          ))
        )}
      </TabsContent>

      <TabsContent value="rejected" className="space-y-2 pt-2">
        <p className="text-muted-foreground text-xs">
          The evidence could not fully confirm these claims. For an{" "}
          <strong>UNSUPPORTED</strong> claim this is a normal outcome — a generated
          paraphrase that no single chunk entails verbatim, not a hallucination
          signal — and the claim still appears in the answer above. Only{" "}
          <strong>CONTRADICTED</strong> claims are withheld from the answer, because
          the evidence actively disagrees with them.
        </p>
        {rejectedClaims.length === 0 ? (
          <EmptyState
            title="Nothing flagged"
            description="Every claim was confirmed by the evidence."
          />
        ) : (
          rejectedClaims.map((claim, i) => (
            <ClaimCard
              key={i}
              claim={claim}
              onOpenChunk={onOpenChunk}
              citationsAreExhaustive={citationsAreExhaustive}
            />
          ))
        )}
      </TabsContent>

      <TabsContent value="refusals" className="space-y-2 pt-2">
        <p className="text-muted-foreground text-xs">
          The system declined to make these claims at all — nothing here was scored
          against evidence, so a refusal is not itself a finding one way or the
          other.
        </p>
        {refusals.length === 0 ? (
          <EmptyState title="No refusals" description="Nothing was declined in this response." />
        ) : (
          refusals.map((claim, i) => (
            <ClaimCard
              key={i}
              claim={claim}
              onOpenChunk={onOpenChunk}
              citationsAreExhaustive={citationsAreExhaustive}
            />
          ))
        )}
      </TabsContent>
    </Tabs>
  );
}
