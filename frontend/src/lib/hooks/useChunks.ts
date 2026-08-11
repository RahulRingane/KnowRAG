import { useQuery } from "@tanstack/react-query";

import { chunks } from "@/lib/api/endpoints";

import { chunksKey } from "./query-keys";

/**
 * `GET /chunks?ids=...` — resolves evidence chunk text for an array of
 * canonical `"documentId:chunkIndex"` keys (e.g. citation chips in the
 * answer, or a claim's `chunk_ids`). `enabled: ids.length > 0`: an
 * evidence panel with nothing selected yet shouldn't fire a network call
 * with an empty `ids` param.
 */
export function useChunks(ids: string[]) {
  return useQuery({
    queryKey: chunksKey(ids),
    queryFn: () => chunks(ids),
    enabled: ids.length > 0,
  });
}
