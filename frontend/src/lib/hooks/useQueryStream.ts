"use client";

import { useCallback, useRef, useState } from "react";

import { queryStream } from "@/lib/api/endpoints";
import type { FactCheckedResponse, QueryRequest, StreamEvent } from "@/types/api";

/**
 * Drives `POST /query/stream`. Deliberately NOT a TanStack Query hook:
 * `useQuery` models a cacheable request/response and `useMutation` a
 * single fire-and-forget call, and neither has a first-class notion of "a
 * sequence of typed events arriving over time, ending in a result." This
 * hand-rolls that lifecycle with plain React state instead of forcing SSE
 * through either abstraction.
 */

export type QueryStreamPhase = "idle" | "connecting" | "streaming" | "done" | "error";

export interface UseQueryStreamState {
  phase: QueryStreamPhase;
  /** Every event seen so far, in arrival order — useful for a debug/trace
   *  view. `token` events here carry raw JSON fragments of the claim
   *  schema, NOT prose (see `lib/api/sse.ts`): render them only as a live
   *  activity indicator ("generating…"), never as answer text. Nothing
   *  before the terminal `verification` event has been fact-checked. */
  events: StreamEvent[];
  retrievedChunkIds: string[] | null;
  result: FactCheckedResponse | null;
  error: Error | null;
}

const INITIAL_STATE: UseQueryStreamState = {
  phase: "idle",
  events: [],
  retrievedChunkIds: null,
  result: null,
  error: null,
};

export function useQueryStream() {
  const [state, setState] = useState<UseQueryStreamState>(INITIAL_STATE);
  const abortRef = useRef<AbortController | null>(null);

  const start = useCallback(async (request: QueryRequest) => {
    // A previous in-flight stream from this hook instance is superseded,
    // not queued alongside the new one — starting a second query mid-stream
    // means the first stream's eventual result is no longer wanted.
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setState({ ...INITIAL_STATE, phase: "connecting" });

    try {
      for await (const event of queryStream(request, { signal: controller.signal })) {
        setState((prev) => {
          const events = [...prev.events, event];
          switch (event.event) {
            case "retrieval":
              return { ...prev, phase: "streaming", events, retrievedChunkIds: event.data.retrieved_chunk_ids };
            case "verification":
              return { ...prev, phase: "streaming", events, result: event.data };
            case "done":
              return { ...prev, phase: "done", events };
            case "error":
              return { ...prev, phase: "error", events, error: new Error(event.data.detail) };
            case "token":
            default:
              return { ...prev, phase: "streaming", events };
          }
        });
      }
    } catch (error) {
      if (controller.signal.aborted) return; // deliberate cancellation, not a failure to surface
      setState((prev) => ({
        ...prev,
        phase: "error",
        error: error instanceof Error ? error : new Error(String(error)),
      }));
    }
  }, []);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    setState((prev) => (prev.phase === "connecting" || prev.phase === "streaming" ? { ...prev, phase: "idle" } : prev));
  }, []);

  return { ...state, start, cancel };
}
