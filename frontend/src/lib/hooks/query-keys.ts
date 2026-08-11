/**
 * Centralised TanStack Query key builders. Every hook that reads a
 * resource, and every mutation that needs to invalidate it, imports its
 * key from here instead of writing the array literal inline — a typo'd
 * key (`["document"]` vs `["documents"]`) silently breaks cache
 * invalidation with no error anywhere, so there is exactly one spelling.
 */

export const documentsKey = () => ["documents"] as const;
export const ingestStatusKey = (documentId: number) => ["ingest-status", documentId] as const;
export const chunksKey = (ids: string[]) => ["chunks", ...ids] as const;
export const healthKey = () => ["health"] as const;
export const currentUserKey = () => ["auth", "me"] as const;
