"use client";

import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

/**
 * Staged loading copy for the NON-streaming (`POST /query`) path. §9 of the
 * WS-D brief: warm queries are 2.7–7.4s and cold ones run up to ~25s, and
 * that spread is the dominant UX state, not an edge case — a bare spinner
 * for up to 25 seconds reads as a hang. The streaming path gets the same
 * expectation-setting for free from `StreamingActivity`'s phase copy
 * (`retrieval` vs `generating`); this component exists because the
 * non-streaming path has no phases to narrate, only a single `isPending`
 * boolean, so it narrates elapsed time instead.
 *
 * Mounted only while a request is pending (parent conditionally renders
 * it), so its internal timer always starts at the request's actual start.
 */
export function PendingNotice() {
  const [stage, setStage] = useState<0 | 1 | 2>(0);

  useEffect(() => {
    const toWarm = setTimeout(() => setStage(1), 3_000);
    const toCold = setTimeout(() => setStage(2), 9_000);
    return () => {
      clearTimeout(toWarm);
      clearTimeout(toCold);
    };
  }, []);

  const message =
    stage === 0
      ? "Retrieving evidence and checking it…"
      : stage === 1
        ? "Still working — a typical warm query takes 2.7–7.4s."
        : "This is taking a while — likely a cold start (model weights loading), which can run up to ~25s.";

  return (
    <div
      role="status"
      aria-live="polite"
      className="text-muted-foreground flex items-center gap-2 text-sm"
    >
      <Loader2 className="size-4 animate-spin" aria-hidden="true" />
      <span>{message}</span>
    </div>
  );
}
