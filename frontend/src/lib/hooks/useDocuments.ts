import { useQuery } from "@tanstack/react-query";

import { documents } from "@/lib/api/endpoints";

import { documentsKey } from "./query-keys";

/** `GET /documents` — the full ingested-document list. */
export function useDocuments() {
  return useQuery({
    queryKey: documentsKey(),
    queryFn: () => documents(),
  });
}
