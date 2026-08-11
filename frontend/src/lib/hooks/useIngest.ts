import { useMutation, useQueryClient } from "@tanstack/react-query";

import { ingest } from "@/lib/api/endpoints";
import type { IngestAccepted } from "@/types/api";

import { documentsKey } from "./query-keys";

/**
 * `POST /ingest` returns `202` immediately — accepted, NOT yet searchable
 * (plan §1.3). This mutation only reports the accept; callers must chain
 * `useIngestStatus(document_id)` to learn when indexing actually finishes.
 * Invalidates the documents list on success so a document list view picks
 * up the new (pending) row without a manual refetch.
 */
export function useIngest() {
  const queryClient = useQueryClient();
  return useMutation<IngestAccepted, Error, File>({
    mutationFn: (file) => ingest(file),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: documentsKey() });
    },
  });
}
