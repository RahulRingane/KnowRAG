"use client";

import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";

import { EmptyState } from "@/components/layout/empty-state";
import { ErrorState } from "@/components/layout/error-state";
import { CardSkeleton } from "@/components/layout/skeletons";
import { EvidencePanel } from "@/components/verification/evidence-panel";
import { VerificationResult } from "@/components/verification/verification-result";
// `RERUN_QUERY_PARAM` is WS-E's constant (`history-list.tsx`), exported at
// integration time specifically so the re-run link's query-string param
// name lives in exactly one place — retyping the literal "q" here would
// let the two silently drift if it ever changed.
import { RERUN_QUERY_PARAM } from "@/components/history/history-list";
import { useQuerySubmit, useQueryStream } from "@/lib/hooks";
import { addHistoryEntry } from "@/lib/storage/history";
import { ApiError, type FactCheckedResponse } from "@/types/api";

import { PendingNotice } from "./pending-notice";
import { QueryComposer } from "./query-composer";
import { StreamingActivity } from "./streaming-activity";

/** Records one completed query into local history (`lib/storage/history.ts`,
 *  WS-E — the module was shipped complete but unwired; see its header
 *  comment for the intended call). Shared by both transports below so
 *  there's exactly one place that decides what a "completed query" writes. */
function recordHistory(response: FactCheckedResponse): void {
  addHistoryEntry({
    question: response.question,
    inputType: response.input_type,
    state: response.state,
  });
}

/**
 * The client island `(app)/page.tsx` delegates to. Owns every piece of
 * state this workstream needs: the composer's text and streaming toggle,
 * which of the two transports (`useQuerySubmit` vs `useQueryStream`) is
 * live, and the evidence panel's open/focused-chunk state — the last of
 * those has to live here rather than in `VerificationResult` because a
 * citation chip inside the answer and a "cited chunks" chip inside a claim
 * card both need to open the SAME panel instance.
 *
 * This is the "API-touching component" §5.3's four-state rule is written
 * for: idle (nothing submitted), loading (either transport in flight),
 * error (either transport failed), and the loaded result. There's no
 * separate top-level "empty" state to render — `POST /query` always
 * resolves to a fully-populated `FactCheckedResponse` (an
 * `insufficient_evidence` state is not an empty response, it's a normal one
 * — trap #1) — so the "nothing to show" cases that DO exist (zero claims,
 * zero rejected claims, zero retrieved chunks) are each handled locally by
 * the component that owns that specific list (`ClaimsPanel`,
 * `EvidencePanel`) instead of being duplicated up here.
 */
export function QueryWorkspace() {
  // Prefill from `/history`'s re-run link (`/?q=<question>`), read once on
  // mount via lazy `useState` initialization — NOT auto-submitted. A link
  // click firing a billed LLM call with no explicit user action would be
  // unacceptable; this only seeds the textarea and leaves `handleSubmit`
  // gated on the user pressing Submit, same as typing the text by hand.
  // `useSearchParams()` is why this component must stay inside the
  // `<Suspense>` boundary `(app)/page.tsx` wraps it in — see that file.
  const searchParams = useSearchParams();
  const [text, setText] = useState(() => searchParams.get(RERUN_QUERY_PARAM) ?? "");
  const [streaming, setStreaming] = useState(true);
  const [lastSubmitted, setLastSubmitted] = useState<string | null>(null);

  const [selectedChunkId, setSelectedChunkId] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);

  const querySubmit = useQuerySubmit();
  const queryStream = useQueryStream();

  // Guards the streaming path's history write: `queryStream.result` lives
  // in React state set once on the `verification` event and then left
  // untouched through the `done` event and any later re-render, but a
  // *new* query resets it to `null` before a *different* response object
  // eventually lands there. Tracking the last-recorded object BY REFERENCE
  // (not a boolean, not "did we see a result this session") is what makes
  // "record exactly once per completed query" hold even across re-renders
  // that don't change `result` at all — a naive `useEffect` keyed on
  // `result` with no such guard is exactly the duplicate-write trap this
  // exists to avoid if this effect is ever touched again.
  const recordedStreamResultRef = useRef<FactCheckedResponse | null>(null);
  useEffect(() => {
    const result = queryStream.result;
    if (result && result !== recordedStreamResultRef.current) {
      recordedStreamResultRef.current = result;
      recordHistory(result);
    }
  }, [queryStream.result]);

  function runQuery(question: string) {
    setLastSubmitted(question);
    if (streaming) {
      void queryStream.start({ question });
    } else {
      // The non-streaming history write rides `mutate`'s own `onSuccess`
      // callback rather than a `useEffect` on `querySubmit.data`: TanStack
      // fires it exactly once per `mutate()` call, only on that call's own
      // success, and — per `useQuerySubmit`'s doc comment — never at all
      // for a call that was cancelled (`cancel()` detaches the observer
      // before an aborted request settles). One callback, one call, one
      // possible write; no ref needed here the way the streaming path
      // needs one.
      querySubmit.mutate({ question }, { onSuccess: recordHistory });
    }
  }

  function handleSubmit() {
    const question = text.trim();
    if (!question) return;
    runQuery(question);
  }

  function handleRetry() {
    if (lastSubmitted) runQuery(lastSubmitted);
  }

  function handleCancel() {
    if (streaming) {
      queryStream.cancel();
    } else {
      // `useQuerySubmit` now owns a real `AbortController` (integration-time
      // fix — see its doc comment) and threads the signal into `query()`,
      // so this actually aborts the in-flight `POST /query` server-side
      // instead of only stopping the UI from waiting on it. Do not
      // "simplify" this back to `querySubmit.reset()`: that was the
      // original bug — the request (and its billed LLM call, on the
      // question route) kept running after the UI moved on.
      querySubmit.cancel();
    }
  }

  function handleOpenChunk(chunkId: string) {
    setSelectedChunkId(chunkId);
    setPanelOpen(true);
  }

  const result = streaming ? queryStream.result : (querySubmit.data ?? null);
  const isBusy = streaming
    ? queryStream.phase === "connecting" || queryStream.phase === "streaming"
    : querySubmit.isPending;
  const error = streaming ? queryStream.error : querySubmit.error;
  const hasRun = streaming ? queryStream.phase !== "idle" : querySubmit.isIdle === false;

  return (
    <div className="space-y-6">
      <QueryComposer
        value={text}
        onValueChange={setText}
        streaming={streaming}
        onStreamingChange={setStreaming}
        onSubmit={handleSubmit}
        onCancel={handleCancel}
        isBusy={isBusy}
      />

      {streaming ? (
        <StreamingActivity
          phase={queryStream.phase}
          events={queryStream.events}
          retrievedChunkIds={queryStream.retrievedChunkIds}
          hasResult={queryStream.result !== null}
        />
      ) : querySubmit.isPending ? (
        <PendingNotice />
      ) : null}

      {error ? (
        <ErrorState
          message="The query failed."
          detail={
            error instanceof ApiError
              ? error.retryAfter
                ? `${error.detail} (retry after ${error.retryAfter}s)`
                : error.detail
              : error.message
          }
          onRetry={lastSubmitted ? handleRetry : undefined}
        />
      ) : isBusy && !result ? (
        <div className="space-y-3">
          <CardSkeleton />
          <CardSkeleton />
        </div>
      ) : result ? (
        <VerificationResult response={result} onOpenChunk={handleOpenChunk} />
      ) : hasRun ? null : (
        <EmptyState
          title="Ask something to get started"
          description='Try a question ("what is RISC?") or a statement to fact-check ("RISC has instruction pipelining").'
        />
      )}

      <EvidencePanel
        open={panelOpen}
        onOpenChange={setPanelOpen}
        chunkIds={result?.retrieved_chunk_ids ?? []}
        focusedChunkId={selectedChunkId}
      />
    </div>
  );
}
