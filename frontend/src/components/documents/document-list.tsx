"use client";

import { FileStack } from "lucide-react";

import { EmptyState } from "@/components/layout/empty-state";
import { ErrorState } from "@/components/layout/error-state";
import { ListSkeleton } from "@/components/layout/skeletons";
import { useDocuments } from "@/lib/hooks";
import { formatTimestamp } from "@/lib/utils/format";
import { ApiError } from "@/types/api";

import { DocumentStatusBadge } from "./document-status-badge";

/**
 * `GET /documents` list — filename, status, chunk count, ingested
 * timestamp. Refetches automatically whenever `DocumentUploadPanel`
 * invalidates the `documents` query key (a new upload accepted, or an
 * in-flight ingest reaching a terminal status), since both components
 * share the same TanStack Query cache entry — no prop wiring between them
 * is needed.
 */
export function DocumentList() {
  const query = useDocuments();

  if (query.isPending) {
    return <ListSkeleton rows={4} />;
  }

  if (query.isError) {
    return (
      <ErrorState
        message="Couldn't load documents"
        detail={query.error instanceof ApiError ? query.error.detail : query.error.message}
        onRetry={() => query.refetch()}
      />
    );
  }

  if (query.data.length === 0) {
    return (
      <EmptyState
        icon={FileStack}
        title="No documents yet"
        description="Upload a PDF above to add it to the corpus."
      />
    );
  }

  return (
    <ul
      className="divide-border border-border divide-y overflow-hidden rounded-lg border"
      aria-label="Ingested documents"
    >
      {query.data.map((doc) => (
        <li key={doc.document_id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
          <div className="min-w-0">
            <p className="truncate text-sm font-medium">{doc.filename}</p>
            <p className="text-muted-foreground text-xs">
              {doc.chunk_count} chunk{doc.chunk_count === 1 ? "" : "s"} · ingested{" "}
              {formatTimestamp(doc.ingested_at)}
            </p>
            {doc.status === "failed" && doc.error ? (
              <p className="text-destructive text-xs">{doc.error}</p>
            ) : null}
          </div>
          <DocumentStatusBadge status={doc.status} />
        </li>
      ))}
    </ul>
  );
}
