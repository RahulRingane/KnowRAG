"use client";

import { AlertTriangle, CheckCircle2, RefreshCw } from "lucide-react";

import { EmptyState } from "@/components/layout/empty-state";
import { ErrorState } from "@/components/layout/error-state";
import { ListSkeleton } from "@/components/layout/skeletons";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useHealth } from "@/lib/hooks";
import { ApiError } from "@/types/api";

import { DatastoreRow } from "./datastore-row";

const DATASTORE_ORDER = ["postgres", "qdrant", "elasticsearch"] as const;

/** How often to re-poll `/health` while this page is open. A dashboard
 *  benefits from staying current; 15s is frequent enough to catch a
 *  datastore flapping without hammering an endpoint every other page in
 *  the app treats as a one-off read. */
const REFETCH_INTERVAL_MS = 15_000;

/**
 * `GET /health` panel. Both `200` and `503` are "success" as far as
 * `useHealth` / `endpoints.health()` are concerned (domain trap #2: same
 * body shape either way) — a `degraded` report renders here as a normal
 * result, never through this component's error state. The error state
 * below is reserved for the request itself failing (network error,
 * unparseable body, an unexpected status outside `[200, 503]`), which
 * `useHealth` surfaces as `isError`.
 */
export function HealthPanel() {
  const query = useHealth(REFETCH_INTERVAL_MS);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-muted-foreground text-sm">Refreshes automatically every 15s.</p>
        <Button variant="outline" size="sm" onClick={() => query.refetch()} disabled={query.isFetching}>
          <RefreshCw className={query.isFetching ? "animate-spin" : undefined} aria-hidden="true" />
          Refresh
        </Button>
      </div>

      {/* Announces status changes to assistive tech on every poll/refresh
          (§5.3: "aria-live=polite on ... the health regions"). */}
      <div aria-live="polite" aria-busy={query.isPending} className="space-y-4">
        {query.isPending ? <ListSkeleton rows={3} /> : null}

        {query.isError ? (
          <ErrorState
            message="Couldn't reach the health endpoint"
            detail={query.error instanceof ApiError ? query.error.detail : query.error.message}
            onRetry={() => query.refetch()}
          />
        ) : null}

        {query.data ? (
          <>
            <Alert variant={query.data.status === "ok" ? "default" : "destructive"}>
              {query.data.status === "ok" ? (
                <CheckCircle2 aria-hidden="true" />
              ) : (
                <AlertTriangle aria-hidden="true" />
              )}
              <AlertTitle>
                {query.data.status === "ok" ? "All systems operational" : "Degraded"}
              </AlertTitle>
              <AlertDescription>
                {query.data.status === "ok"
                  ? `${query.data.service} and every datastore it depends on are reachable.`
                  : "One or more datastores are unreachable — see the breakdown below."}
              </AlertDescription>
            </Alert>

            {Object.keys(query.data.checks).length === 0 ? (
              <EmptyState
                title="No datastore checks reported"
                description="The health endpoint returned no per-datastore breakdown."
              />
            ) : (
              <ul className="divide-border border-border divide-y overflow-hidden rounded-lg border">
                {DATASTORE_ORDER.map((name) => {
                  const check = query.data.checks[name];
                  if (!check) return null;
                  return <DatastoreRow key={name} name={name} check={check} />;
                })}
              </ul>
            )}
          </>
        ) : null}
      </div>
    </div>
  );
}
