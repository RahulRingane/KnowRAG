import { useCallback, useRef } from "react";
import { useMutation } from "@tanstack/react-query";

import { query } from "@/lib/api/endpoints";
import type { FactCheckedResponse, QueryRequest } from "@/types/api";

/**
 * Fires `POST /query` once per call. Deliberately a `useMutation`, not a
 * `useQuery`: a query/fact-check is a user-initiated action with a real
 * side effect (an LLM call on the backend, ~$0.0006–0.0011 per question on
 * the question route — see CLAUDE.md "LLM provider"), not idle data that
 * should be silently refetched on mount, window refocus, or a stale-time
 * expiry. `retry` is left at the `QueryClient` mutation default of `0`
 * (see `app/providers.tsx`) rather than overridden here — auto-retrying an
 * already-billed call is the wrong default; a 503 with `Retry-After` is a
 * UI-level "try again" decision, not an automatic one.
 *
 * --- Real cancellation (integration-time fix) ---
 * TanStack v5 does NOT inject an `AbortSignal` into a `mutationFn` the way
 * it injects one into `useQuery`'s `queryFn` — that plumbing is a
 * `useQuery`-only feature. Left alone, `mutate()`'s underlying `fetch()`
 * has no way to be aborted, which is exactly the gap WS-D found and left
 * flagged in `query-workspace.tsx`: the old "Cancel" button called the
 * mutation's `reset()`, which stops *this component* from waiting on the
 * call, but the `POST /query` request — and its billed LLM call, on the
 * question route — kept running server-side regardless.
 *
 * This hook now owns the `AbortController` itself and threads its
 * `signal` into `query()` (`lib/api/endpoints.ts` already accepts one via
 * `EndpointCallOptions`), so `cancel()` aborts the real request, not just
 * this hook's view of it — the same shape as `useQueryStream`'s
 * `abortRef`.
 *
 * A fresh `mutate()` call aborts whatever controller is still outstanding
 * before issuing its own fetch, mirroring `useQueryStream.start()`'s "a
 * previous in-flight call is superseded, not queued alongside the new
 * one." (`@tanstack/query-core`'s `MutationObserver.mutate()` already
 * detaches this hook's observer from the stale `Mutation` on every call,
 * so the old call's eventual settlement can never clobber the new call's
 * state either way — but only this explicit `abort()` stops the old
 * request from actually finishing, and being billed, on the server.)
 */
export function useQuerySubmit() {
  const abortRef = useRef<AbortController | null>(null);

  const mutation = useMutation<FactCheckedResponse, Error, QueryRequest>({
    mutationFn: (request) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      return query(request, { signal: controller.signal });
    },
  });

  const { reset } = mutation;

  /**
   * Aborts the in-flight request (if any) and resets the mutation back to
   * idle. `reset()` (`@tanstack/query-core`'s `MutationObserver.reset()`)
   * removes this hook's observer from the underlying `Mutation` object, so
   * even though the aborted `fetch` still rejects a moment later (`apiRequest`
   * turns an `AbortError` into a plain `ApiError` — see `lib/api/client.ts`
   * — there is no distinguishable error shape to filter on after the
   * fact), this hook is no longer listening for it:
   *
   *  - it never reaches `mutation.error`, which is what keeps an
   *    intentional cancel from surfacing as a user-facing error state
   *    (mirrors `useQueryStream`'s own `if (controller.signal.aborted)
   *    return;` short-circuit in its catch block), and
   *  - the `onSuccess` callback `query-workspace.tsx` passes to `mutate()`
   *    (where the history write happens) never fires for a call that's
   *    been `reset()`, so a cancelled query is never recorded into
   *    history.
   *
   * Do not "simplify" this back to a bare `reset()` — that was the
   * original bug: it looks like a cancel because the UI stops waiting, but
   * the request (and its LLM cost) finishes anyway.
   */
  const cancel = useCallback(() => {
    abortRef.current?.abort();
    reset();
  }, [reset]);

  return { ...mutation, cancel };
}
