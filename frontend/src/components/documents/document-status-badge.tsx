import { CheckCircle2, Clock, XCircle, type LucideIcon } from "lucide-react";
import type { VariantProps } from "class-variance-authority";

import { Badge, badgeVariants } from "@/components/ui/badge";
import type { DocumentStatusValue } from "@/types/api";

const DOCUMENT_STATUS_META: Record<
  DocumentStatusValue,
  { label: string; icon: LucideIcon; variant: VariantProps<typeof badgeVariants>["variant"] }
> = {
  pending: { label: "Pending", icon: Clock, variant: "secondary" },
  indexed: { label: "Indexed", icon: CheckCircle2, variant: "outline" },
  failed: { label: "Failed", icon: XCircle, variant: "destructive" },
};

/**
 * Status badge for `IngestStatus.status` ("pending" | "indexed" |
 * "failed"). This is a document lifecycle state, not an NLI claim
 * verdict, so it deliberately does NOT go through `lib/utils/verdict.ts`
 * — that map is reserved for `ClaimStatus` / `QueryState` (§5.3: "never
 * hardcode a verdict colour"). Colour still comes from the shared `Badge`
 * variant system (secondary/outline/destructive) rather than a one-off
 * class, and every variant pairs an icon with the label text.
 */
export function DocumentStatusBadge({ status }: { status: DocumentStatusValue }) {
  const meta = DOCUMENT_STATUS_META[status];
  const Icon = meta.icon;
  return (
    <Badge variant={meta.variant} data-icon="inline-start">
      <Icon aria-hidden="true" />
      {meta.label}
    </Badge>
  );
}
