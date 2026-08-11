import { refreshAccessToken } from "@/lib/auth/refresh";
import { AuthUserSchema, TokenPairSchema } from "@/lib/schemas/auth";
import { ChunksResponseSchema } from "@/lib/schemas/chunks";
import { DocumentsResponseSchema, IngestAcceptedSchema, IngestStatusSchema } from "@/lib/schemas/ingest";
import { HealthReportSchema } from "@/lib/schemas/health";
import { parseOrThrow } from "@/lib/schemas/parse";
import { FactCheckedResponseSchema } from "@/lib/schemas/query";
import type {
  AuthUser,
  ChunksResponse,
  Credentials,
  DocumentsResponse,
  FactCheckedResponse,
  HealthReport,
  IngestAccepted,
  IngestStatus,
  QueryRequest,
  StreamEvent,
  TokenPair,
} from "@/types/api";

import { apiRequest } from "./client";
import { streamQuery, type StreamQueryOptions } from "./sse";

/**
 * One typed function per backend route. Every function here does exactly
 * two things: call `apiRequest` (the transport) and `parseOrThrow` the
 * result against the matching Zod schema (the boundary validation) — no
 * business logic, no state mutation. Auth-state side effects (storing a
 * token after login, clearing it after logout) deliberately live in
 * `lib/hooks/useAuth.ts`, not here, so this file stays pure HTTP transport
 * that `lib/hooks` composes however it needs to.
 */

export interface EndpointCallOptions {
  signal?: AbortSignal;
}

/** `POST /query` — classify → route → `FactCheckedResponse`. Request body
 *  is always `{question}`, even when the text is a statement bound for the
 *  fact-check route (plan §1.1); the backend does the routing. */
export function query(request: QueryRequest, opts: EndpointCallOptions = {}): Promise<FactCheckedResponse> {
  return apiRequest<unknown>({
    path: "/query",
    method: "POST",
    body: request,
    signal: opts.signal,
  }).then((data) => parseOrThrow(FactCheckedResponseSchema, data, "POST /query"));
}

/**
 * `POST /query/stream`, re-exported here so callers get one import surface
 * (`@/lib/api/endpoints`) for every route. The transport lives in
 * `./sse.ts` rather than going through `apiRequest` because it never
 * parses a single JSON body — it yields typed frames as they arrive.
 */
export function queryStream(
  request: QueryRequest,
  opts: StreamQueryOptions = {},
): AsyncGenerator<StreamEvent, void, void> {
  return streamQuery(request, opts);
}

/** `POST /ingest` — multipart, field name `file` per the backend route
 *  (`routes/ingest.py`). Returns `202` semantics: accepted, NOT yet
 *  searchable. Callers must poll `ingestStatus()` (see
 *  `lib/hooks/useIngestStatus.ts`) until `status` reaches `"indexed"`. */
export function ingest(file: File, opts: EndpointCallOptions = {}): Promise<IngestAccepted> {
  const formData = new FormData();
  formData.append("file", file);
  return apiRequest<unknown>({
    path: "/ingest",
    method: "POST",
    formData,
    signal: opts.signal,
  }).then((data) => parseOrThrow(IngestAcceptedSchema, data, "POST /ingest"));
}

/** `GET /ingest/{id}` — poll target for ingestion progress. `404` on an
 *  unknown id surfaces as a rejected `ApiError`, unchanged. */
export function ingestStatus(documentId: number, opts: EndpointCallOptions = {}): Promise<IngestStatus> {
  return apiRequest<unknown>({
    path: `/ingest/${documentId}`,
    signal: opts.signal,
  }).then((data) => parseOrThrow(IngestStatusSchema, data, `GET /ingest/${documentId}`));
}

/**
 * `GET /documents`. Its exact response shape is NOT part of the frozen
 * §5.2 contract — it's a gap-fill endpoint backend WS-0 added, documented
 * in plan §1.3/§2 only as "enumerate ingested documents". Modeled here as
 * a bare JSON array of `IngestStatus`-shaped rows (FastAPI's usual
 * `List[...]` convention for a list endpoint). **Confirm against the live
 * backend in WS-J** — if it turns out to be `{documents: [...]}` or paged,
 * only this function, `DocumentsResponseSchema`
 * (`lib/schemas/ingest.ts`), and `DocumentsResponse` (`types/api.ts`) need
 * to change; nothing above the hooks layer should be affected.
 */
export function documents(opts: EndpointCallOptions = {}): Promise<DocumentsResponse> {
  return apiRequest<unknown>({
    path: "/documents",
    signal: opts.signal,
  }).then((data) => parseOrThrow(DocumentsResponseSchema, data, "GET /documents"));
}

/**
 * `GET /chunks?ids=1:3,1:5`. Same caveat as `documents()` above: plan
 * §1.4 documents *behaviour* ("unknown ids simply absent, not a 404";
 * malformed keys and >200 ids are `400`) but not the exact JSON shape.
 * Modeled as a bare array of the `ChunkText` records that were found —
 * not a map keyed by chunk key — because `ChunkText` already carries
 * `document_id`/`chunk_index`, and `formatChunkKey()`
 * (`lib/utils/chunk-key.ts`) can derive the key client-side if a caller
 * wants a lookup map. **Confirm against the live backend in WS-J.**
 *
 * `ids` are joined with `,` before being handed to `apiRequest`'s
 * `URLSearchParams`-based query builder, which percent-encodes the value;
 * FastAPI/Starlette decode query values before the route sees them, so the
 * backend still receives the literal `"1:3,1:5"` the plan's example shows.
 */
export function chunks(ids: string[], opts: EndpointCallOptions = {}): Promise<ChunksResponse> {
  return apiRequest<unknown>({
    path: "/chunks",
    query: { ids: ids.join(",") },
    signal: opts.signal,
  }).then((data) => parseOrThrow(ChunksResponseSchema, data, "GET /chunks"));
}

/** `GET /health` — open endpoint, no token sent (`auth: false`). Returns
 *  the SAME body shape on `200` and `503` (plan §1.4), so `okStatuses`
 *  treats both as success: the caller must read `status`/`checks`, not
 *  the HTTP status code, to know what's actually down. */
export function health(opts: EndpointCallOptions = {}): Promise<HealthReport> {
  return apiRequest<unknown>({
    path: "/health",
    auth: false,
    okStatuses: [200, 503],
    signal: opts.signal,
  }).then((data) => parseOrThrow(HealthReportSchema, data, "GET /health"));
}

/** `POST /auth/login` — `auth: false` (this call is what *obtains* the
 *  first token) but `credentials: 'include'` is still required: the
 *  refresh cookie is set via `Set-Cookie` on this very response, and a
 *  cross-origin fetch (app on :3000, API on :8000/:8001) only stores an
 *  incoming `Set-Cookie` when the request that received it used
 *  `'include'`. Does NOT store the returned access token itself — that's
 *  `lib/hooks/useAuth.ts#useLogin`'s job on success. */
export function login(credentials: Credentials, opts: EndpointCallOptions = {}): Promise<TokenPair> {
  return apiRequest<unknown>({
    path: "/auth/login",
    method: "POST",
    body: credentials,
    auth: false,
    credentials: "include",
    signal: opts.signal,
  }).then((data) => parseOrThrow(TokenPairSchema, data, "POST /auth/login"));
}

/** `POST /auth/register` — works only while no user exists yet; `403`
 *  ("Registration is closed…") afterwards, surfaced as an `ApiError` for
 *  the caller (WS-C) to render. No cookie is set here, so no `credentials`
 *  override is needed. */
export function register(credentials: Credentials, opts: EndpointCallOptions = {}): Promise<AuthUser> {
  return apiRequest<unknown>({
    path: "/auth/register",
    method: "POST",
    body: credentials,
    auth: false,
    signal: opts.signal,
  }).then((data) => parseOrThrow(AuthUserSchema, data, "POST /auth/register"));
}

/**
 * Explicit `POST /auth/refresh`. Delegates to the same single-flight
 * `refreshAccessToken()` used internally by the 401 interceptor
 * (`client.ts`) and the SSE pre-stream check (`sse.ts`), rather than
 * issuing its own independent fetch — so a caller that invokes `refresh()`
 * directly (e.g. `bootstrapSession()` in WS-C's `AuthProvider`) at the
 * same moment the interceptor is *also* refreshing collapses onto the
 * exact same in-flight request instead of racing it and rotating the
 * cookie twice.
 */
export function refresh(): Promise<string | null> {
  return refreshAccessToken();
}

/** `POST /auth/logout` — bearer-authenticated, `204` on success, and
 *  `credentials: 'include'` because the server clears the refresh cookie
 *  via `Set-Cookie` on this response, which (as with login) only lands in
 *  the cookie jar for a cross-origin request made with `'include'`. Does
 *  NOT clear the in-memory token itself — see `useLogout` for why that's
 *  a hook-layer concern (it must happen even if this call fails). */
export function logout(opts: EndpointCallOptions = {}): Promise<void> {
  return apiRequest<void>({
    path: "/auth/logout",
    method: "POST",
    credentials: "include",
    okStatuses: [204],
    signal: opts.signal,
  });
}

/** `GET /auth/me` — bearer-authenticated profile lookup. */
export function me(opts: EndpointCallOptions = {}): Promise<AuthUser> {
  return apiRequest<unknown>({
    path: "/auth/me",
    signal: opts.signal,
  }).then((data) => parseOrThrow(AuthUserSchema, data, "GET /auth/me"));
}
