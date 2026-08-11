import { env } from "@/config/env";
import { isExemptFromRefresh, refreshAccessToken } from "@/lib/auth/refresh";
import { getAccessToken } from "@/lib/auth/token-store";
import { ApiError } from "@/types/api";

import { buildApiErrorFromResponse } from "./errors";

/**
 * The one `fetch` wrapper every request in this app goes through. Centralising
 * this here — rather than letting each `lib/api/endpoints.ts` function call
 * `fetch` directly — is what makes the token attach, the 60s timeout, and the
 * 401→refresh→retry-once policy live in exactly one place instead of being
 * re-implemented (and re-forgotten) per endpoint.
 */

/** 2.7–7.4s warm, up to ~25s cold (CLAUDE.md, "Latency and the timing
 *  breakdown"). 60s gives real headroom above the documented cold-start
 *  ceiling instead of cutting off a legitimate slow query. */
const DEFAULT_TIMEOUT_MS = 60_000;

export interface ApiRequestOptions {
  /** Path relative to the API base, e.g. `/query`, `/ingest/3`. */
  path: string;
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  /** JSON-serialisable request body. Mutually exclusive with `formData`. */
  body?: unknown;
  /** Multipart body (e.g. `POST /ingest`'s `file` field). When set, no
   *  `Content-Type` header is added — the browser must generate the
   *  multipart boundary itself. Mutually exclusive with `body`. */
  formData?: FormData;
  /** Query-string parameters; `undefined` values are omitted. */
  query?: Record<string, string | number | undefined>;
  /** `false` marks an open endpoint (`/health`, `/`): never attach a
   *  bearer token, and a `401` from it never triggers the refresh
   *  interceptor. Defaults to `true`. */
  auth?: boolean;
  /** Only `/auth/login`, `/auth/refresh`, and `/auth/logout` need
   *  `'include'` to send/receive the HttpOnly, `Path=/auth` refresh
   *  cookie. Defaults to `'same-origin'`. */
  credentials?: RequestCredentials;
  signal?: AbortSignal;
  /** Response statuses treated as success (body parsed and returned)
   *  instead of throwing. Defaults to `response.ok` (2xx). Needed for
   *  `/health`, whose body is meaningful on both `200` and `503` (plan
   *  §1.4) — read the body, not just the status. */
  okStatuses?: number[];
  timeoutMs?: number;
}

function buildUrl(path: string, query?: ApiRequestOptions["query"]): string {
  const url = new URL(path, env.apiBaseUrl);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined) url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

/**
 * Combines a caller-supplied `AbortSignal` (component unmount, user
 * cancel) with an internal timeout into one signal, without depending on
 * `AbortSignal.any` (recent enough that relying on it here felt like an
 * unnecessary compatibility bet for a hand-rolled 10-line alternative).
 */
function withTimeout(timeoutMs: number, externalSignal?: AbortSignal): AbortSignal {
  const controller = new AbortController();
  const timer = setTimeout(() => {
    controller.abort(new DOMException(`Request timed out after ${timeoutMs}ms`, "TimeoutError"));
  }, timeoutMs);

  const onExternalAbort = () => controller.abort(externalSignal?.reason);
  if (externalSignal) {
    if (externalSignal.aborted) onExternalAbort();
    else externalSignal.addEventListener("abort", onExternalAbort, { once: true });
  }

  controller.signal.addEventListener(
    "abort",
    () => {
      clearTimeout(timer);
      externalSignal?.removeEventListener("abort", onExternalAbort);
    },
    { once: true },
  );

  return controller.signal;
}

/**
 * Central fetch wrapper. `attempt` is internal — callers never pass it. It
 * caps the 401-recovery path at a single retry: if the refreshed token
 * *still* gets a 401 (or refresh itself fails), this gives up rather than
 * looping, per plan §3.2 ("retry once, then give up").
 */
export async function apiRequest<T = unknown>(options: ApiRequestOptions, attempt = 0): Promise<T> {
  const {
    path,
    method = "GET",
    body,
    formData,
    query,
    auth = true,
    credentials = "same-origin",
    signal,
    okStatuses,
    timeoutMs = DEFAULT_TIMEOUT_MS,
  } = options;

  const url = buildUrl(path, query);
  const headers: Record<string, string> = { Accept: "application/json" };
  if (!formData && body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (auth) {
    const token = getAccessToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }

  const requestSignal = withTimeout(timeoutMs, signal);

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers,
      credentials,
      signal: requestSignal,
      body: formData ?? (body !== undefined ? JSON.stringify(body) : undefined),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "TimeoutError") {
      throw new ApiError(0, `Request to ${path} timed out after ${timeoutMs}ms.`);
    }
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError(0, `Request to ${path} was aborted.`);
    }
    throw new ApiError(0, `Network error calling ${path}: ${error instanceof Error ? error.message : String(error)}`);
  }

  const isSuccess = okStatuses ? okStatuses.includes(response.status) : response.ok;

  if (!isSuccess) {
    if (response.status === 401 && auth && attempt === 0 && !isExemptFromRefresh(path)) {
      const newToken = await refreshAccessToken();
      if (newToken) {
        return apiRequest<T>(options, attempt + 1);
      }
      // Refresh failed: token-store already cleared and subscribers
      // notified inside refreshAccessToken(). Fall through and surface
      // the original 401 rather than retrying against a dead session.
    }
    throw await buildApiErrorFromResponse(response);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  try {
    return (await response.json()) as T;
  } catch {
    // A 2xx with an empty or non-JSON body (rare, but not an error on its
    // own) resolves to `undefined` rather than throwing a parse error.
    return undefined as T;
  }
}
