import { useQuery } from "@tanstack/react-query";

import { health } from "@/lib/api/endpoints";

import { healthKey } from "./query-keys";

/**
 * `GET /health`. The resolved `HealthReport` already reflects the
 * "same body shape on 200 and 503" rule (plan §1.4) — a `503` never
 * reaches here as a thrown error, since `endpoints.health()` treats both
 * statuses as success and hands back the parsed body either way; render
 * `data.status`/`data.checks`, not `isError`, to show degraded state.
 *
 * `refetchIntervalMs` is left to the caller rather than defaulted, since
 * only a status/dashboard page (WS-E) needs continuous polling — most
 * consumers just want an on-demand read.
 */
export function useHealth(refetchIntervalMs?: number) {
  return useQuery({
    queryKey: healthKey(),
    queryFn: () => health(),
    refetchInterval: refetchIntervalMs,
  });
}
