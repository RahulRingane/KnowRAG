import { useQuery } from "@tanstack/react-query";
import { useRef } from "react";

import { ingestStatus } from "@/lib/api/endpoints";

import { ingestStatusKey } from "./query-keys";

const POLL_INTERVAL_MS = 2_000;
const MAX_POLL_DURATION_MS = 5 * 60_000; // ~5 min cap
const TERMINAL_STATUSES = new Set(["indexed", "failed"]);

/**
 * Polls `GET /ingest/{id}` every 2s until the document reaches a terminal
 * status, then stops — continuing to poll `indexed`/`failed` would be pure
 * wasted load with no new information (plan §6, WS-B deliverables).
 *
 * Also caps total polling at ~5 minutes from the first successful fetch: a
 * document stuck in `pending` (worker crash, unusually large PDF) should
 * surface to the UI as "still pending, taking unusually long" rather than
 * poll silently forever. The cap is measured from data, not from mount, so
 * remounting this hook for the same `documentId` (e.g. navigating away and
 * back) restarts the budget rather than being permanently capped by a
 * stale timestamp.
 */
export function useIngestStatus(documentId: number | null) {
  const startedAtRef = useRef<number | null>(null);

  return useQuery({
    queryKey: ingestStatusKey(documentId ?? -1),
    queryFn: () => {
      startedAtRef.current ??= Date.now();
      return ingestStatus(documentId as number);
    },
    enabled: documentId !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status !== undefined && TERMINAL_STATUSES.has(status)) return false;
      const startedAt = startedAtRef.current;
      if (startedAt !== null && Date.now() - startedAt > MAX_POLL_DURATION_MS) return false;
      return POLL_INTERVAL_MS;
    },
  });
}
