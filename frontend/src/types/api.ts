/**
 * The single source of truth for the backend's wire contract (plan §5.2,
 * authored verbatim from the block WS-A/WS-B froze before parallel work
 * started). Every other module — Zod schemas, `lib/api`, `lib/hooks`, and
 * every downstream component workstream — imports its shapes from here.
 * Do not redefine any of these types elsewhere; do not change field names
 * to "look more TypeScript-y" (e.g. `chunk_ids` stays snake_case because
 * that's the literal JSON key coming off the wire).
 */

export type InputType = "question" | "fact";
export type QueryState = "ok" | "insufficient_evidence" | "contradicted";
export type ClaimStatus = "SUPPORTED" | "UNSUPPORTED" | "CONTRADICTED" | "REFUSAL";
export type DocumentStatusValue = "pending" | "indexed" | "failed";

export interface ClaimVerdict {
  text: string;
  status: ClaimStatus;
  citations: string[]; // ["C1","C3"] — tags
  evidence_score: number | null; // null for REFUSAL
  chunk_ids: string[]; // ["1:3"] — canonical keys
  reason: string | null;
}

export interface FactCheckedResponse {
  input_type: InputType;
  question: string;
  answer: string;
  state: QueryState;
  claims: ClaimVerdict[];
  retrieved_chunk_ids: string[];
  latency_ms: Record<string, number>; // OPEN-ENDED. Never a fixed interface.
  rejected_claims: ClaimVerdict[]; // server-computed projection
  refusals: ClaimVerdict[]; // server-computed projection
}

export interface QueryRequest {
  question: string;
} // named `question` for facts too

export interface IngestAccepted {
  document_id: number;
  filename: string;
  status: string;
}

export interface IngestStatus {
  document_id: number;
  filename: string;
  status: DocumentStatusValue;
  chunk_count: number;
  ingested_at: string | null;
  error: string | null;
}

export interface HealthCheck {
  status: "ok" | "error";
  detail?: string;
}

export interface HealthReport {
  status: "ok" | "degraded";
  service: string;
  checks: Record<"postgres" | "qdrant" | "elasticsearch", HealthCheck>;
}

export interface ChunkText {
  document_id: number;
  chunk_index: number;
  text: string;
}

export interface AuthUser {
  id: number;
  username: string;
  created_at: string;
}

export interface TokenPair {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
}

export interface Credentials {
  username: string;
  password: string;
}

export type StreamEvent =
  | { event: "retrieval"; data: { retrieved_chunk_ids: string[] } }
  | { event: "token"; data: { text: string } } // RAW JSON FRAGMENT — never shown as prose
  | { event: "verification"; data: FactCheckedResponse }
  | { event: "done"; data: Record<string, never> }
  | { event: "error"; data: { detail: string } };

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    readonly retryAfter?: number,
  ) {
    super(detail);
  }
}

/**
 * `chunk_ids` are always `"<documentId>:<chunkIndex>"`. `parseChunkKey()` /
 * `formatChunkKey()` in `lib/utils/chunk-key.ts` are the ONLY place that
 * string is split or built, mirroring the backend's `chunk_key()` rule.
 */

// ---------------------------------------------------------------------------
// Everything below this line is NOT part of the frozen §5.2 block above.
// `GET /documents` and `GET /chunks` were added by backend WS-0, and plan
// §1.3/§1.4 describes their behaviour in prose rather than as a type. WS-B
// modelled them by inference; both were then **CONFIRMED against the backend
// source** (2026-08-11) and are no longer open questions:
//
//   app/api/routes/ingest.py:109  @router.get("/documents", response_model=list[IngestStatus])
//   app/api/routes/corpus.py:20   @router.get("/chunks",    response_model=list[ChunkText])
//
// Both are bare arrays of the shapes already declared above, so these are
// aliases rather than new contracts. WS-J does not need to re-verify them.
// ---------------------------------------------------------------------------

/** `GET /documents` list item — same fields as `GET /ingest/{id}`. */
export type DocumentSummary = IngestStatus;

/** `GET /documents` response: a bare array, FastAPI's usual `List[...]` convention. */
export type DocumentsResponse = DocumentSummary[];

/** `GET /chunks` response: a bare array of the chunks that were found; ids with
 *  no match simply produce no entry (per plan §1.4), rather than a null/error. */
export type ChunksResponse = ChunkText[];
