import { CheckCircle2, XCircle } from "lucide-react";

import { cn } from "@/lib/utils";
import type { HealthCheck } from "@/types/api";

const DATASTORE_LABELS = {
  postgres: "Postgres",
  qdrant: "Qdrant",
  elasticsearch: "Elasticsearch",
} as const;

export interface DatastoreRowProps {
  name: keyof typeof DATASTORE_LABELS;
  check: HealthCheck;
}

/**
 * One row of the `/health` per-datastore breakdown. `HealthCheck.status`
 * ("ok" | "error") is a different, two-state domain from `ClaimStatus` /
 * `QueryState`, so it deliberately does NOT go through
 * `lib/utils/verdict.ts` — that map is reserved for NLI verdicts. Status
 * is still never colour-only: an ok row pairs `CheckCircle2` with "Ok" in
 * the default text colour; an error row pairs `XCircle` with "Error" in
 * `text-destructive` — the same shared error colour every other error
 * state in this app already uses, not a one-off token.
 */
export function DatastoreRow({ name, check }: DatastoreRowProps) {
  const ok = check.status === "ok";
  const Icon = ok ? CheckCircle2 : XCircle;
  return (
    <li className="flex items-start justify-between gap-3 px-4 py-3">
      <div className="min-w-0">
        <p className="text-sm font-medium">{DATASTORE_LABELS[name]}</p>
        {check.detail ? (
          <p className="text-muted-foreground text-xs break-words">{check.detail}</p>
        ) : null}
      </div>
      <span
        className={cn("flex shrink-0 items-center gap-1.5 text-sm font-medium", !ok && "text-destructive")}
      >
        <Icon className="size-4" aria-hidden="true" />
        {ok ? "Ok" : "Error"}
      </span>
    </li>
  );
}
