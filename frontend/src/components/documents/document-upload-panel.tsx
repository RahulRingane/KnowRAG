"use client";

import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Loader2 } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { documentsKey, useIngest, useIngestStatus } from "@/lib/hooks";
import { ApiError } from "@/types/api";

import { UploadDropzone } from "./upload-dropzone";

/**
 * Owns the whole ingest lifecycle: pick/drop a file → `POST /ingest`
 * (`202`, accepted-not-searchable, domain trap #1) → poll
 * `GET /ingest/{id}` via `useIngestStatus` (already 2s-polls and stops on
 * a terminal status — see that hook, don't re-implement polling here) →
 * render `pending` → `indexed` / `failed` (trap #5: `failed` carries an
 * `error` string, shown verbatim).
 *
 * `useIngest()` already invalidates the `documents` list on the `202`
 * accept, so the new `pending` row appears immediately. This component
 * additionally invalidates it again the moment polling first reaches a
 * terminal status, so `chunk_count` / `ingested_at` on that row refresh
 * without a manual reload (plan: "Invalidate the documents query when an
 * ingest reaches a terminal state").
 */
export function DocumentUploadPanel() {
  const queryClient = useQueryClient();
  const ingestMutation = useIngest();
  const [activeDocumentId, setActiveDocumentId] = useState<number | null>(null);
  const statusQuery = useIngestStatus(activeDocumentId);
  // Tracks the last terminal status we've already invalidated for, so a
  // component re-render (e.g. from the interval poll itself) doesn't
  // re-invalidate on every tick once the terminal state is reached.
  const lastNotifiedStatusRef = useRef<string | null>(null);

  const status = statusQuery.data?.status;

  useEffect(() => {
    if (status !== "indexed" && status !== "failed") return;
    if (status === lastNotifiedStatusRef.current) return;
    lastNotifiedStatusRef.current = status;
    queryClient.invalidateQueries({ queryKey: documentsKey() });
  }, [status, queryClient]);

  function handleFileSelected(file: File) {
    lastNotifiedStatusRef.current = null;
    ingestMutation.mutate(file, {
      onSuccess: (accepted) => setActiveDocumentId(accepted.document_id),
    });
  }

  function handleUploadAnother() {
    setActiveDocumentId(null);
    ingestMutation.reset();
  }

  const mutationError = ingestMutation.error;
  const dropzoneErrorMessage = ingestMutation.isPending
    ? null
    : mutationError instanceof ApiError
      ? mutationError.detail
      : mutationError
        ? "Upload failed. Check your connection and try again."
        : null;

  const showDropzone = activeDocumentId === null;
  const isTerminal = status === "indexed" || status === "failed";

  return (
    <div className="space-y-4">
      {showDropzone ? (
        <UploadDropzone
          onFileSelected={handleFileSelected}
          disabled={ingestMutation.isPending}
          errorMessage={dropzoneErrorMessage}
        />
      ) : null}

      {/* Announces upload/index progress to assistive tech as it changes
          (§5.3: "aria-live=polite on the upload-progress ... regions"). */}
      <div aria-live="polite" className="space-y-3">
        {ingestMutation.isPending ? (
          <Alert>
            <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            <AlertTitle>Uploading…</AlertTitle>
            <AlertDescription>Sending the file to the server.</AlertDescription>
          </Alert>
        ) : null}

        {activeDocumentId !== null && !isTerminal ? (
          <Alert>
            <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            <AlertTitle>Indexing…</AlertTitle>
            <AlertDescription>
              {statusQuery.data?.filename ?? "The document"} was accepted and is being chunked and
              indexed. This can take a while for a large PDF — the page keeps checking every couple
              of seconds.
            </AlertDescription>
          </Alert>
        ) : null}

        {status === "indexed" && statusQuery.data ? (
          <Alert>
            <CheckCircle2 className="size-4" aria-hidden="true" />
            <AlertTitle>Indexed</AlertTitle>
            <AlertDescription>
              {statusQuery.data.filename} — {statusQuery.data.chunk_count} chunk
              {statusQuery.data.chunk_count === 1 ? "" : "s"} indexed and searchable.
            </AlertDescription>
          </Alert>
        ) : null}

        {status === "failed" && statusQuery.data ? (
          <Alert variant="destructive">
            <AlertTriangle className="size-4" aria-hidden="true" />
            <AlertTitle>Ingestion failed</AlertTitle>
            <AlertDescription>
              {statusQuery.data.error ?? "The server couldn't index this document."}
            </AlertDescription>
          </Alert>
        ) : null}

        {statusQuery.isError ? (
          <Alert variant="destructive">
            <AlertTriangle className="size-4" aria-hidden="true" />
            <AlertTitle>Couldn&apos;t check ingestion status</AlertTitle>
            <AlertDescription>
              {statusQuery.error instanceof ApiError
                ? statusQuery.error.detail
                : "Lost track of this upload's progress."}
            </AlertDescription>
          </Alert>
        ) : null}
      </div>

      {activeDocumentId !== null && isTerminal ? (
        <Button variant="outline" size="sm" onClick={handleUploadAnother}>
          Upload another document
        </Button>
      ) : null}
    </div>
  );
}
