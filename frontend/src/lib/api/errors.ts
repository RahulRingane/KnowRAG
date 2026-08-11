import { ApiError } from "@/types/api";

/**
 * Shared error-response parsing used by both `client.ts` (JSON requests)
 * and `sse.ts` (the SSE connect handshake, before any frame parsing
 * starts) so the two transports don't grow two slightly different ideas
 * of what a backend error body looks like.
 */

/**
 * Backend errors are `{"detail": "..."}` (FastAPI's default `HTTPException`
 * shape). Falls back to `response.statusText` when the body isn't that
 * shape or isn't JSON at all (e.g. a proxy/gateway error page), so a
 * network intermediary returning HTML never surfaces as an unhandled
 * JSON-parse exception.
 */
export async function parseErrorDetail(response: Response): Promise<string> {
  try {
    const data: unknown = await response.clone().json();
    if (
      data !== null &&
      typeof data === "object" &&
      "detail" in data &&
      typeof (data as { detail: unknown }).detail === "string"
    ) {
      return (data as { detail: string }).detail;
    }
  } catch {
    // Body wasn't JSON — fall through to the status-based message below.
  }
  return response.statusText || `Request failed with status ${response.status}`;
}

/**
 * Parses the `Retry-After` header (seconds) on a `503`, the shape the
 * backend uses to signal "LLM provider out of quota, try again later"
 * (plan §1.1). Returns `undefined` for any other status or a
 * missing/non-numeric header rather than `NaN`, so callers can do a plain
 * truthiness check.
 */
export function parseRetryAfter(response: Response): number | undefined {
  if (response.status !== 503) return undefined;
  const header = response.headers.get("Retry-After");
  if (!header) return undefined;
  const seconds = Number(header);
  return Number.isFinite(seconds) ? seconds : undefined;
}

/** Builds the `ApiError` for a non-ok `Response`, reading its body once. */
export async function buildApiErrorFromResponse(response: Response): Promise<ApiError> {
  const detail = await parseErrorDetail(response);
  const retryAfter = parseRetryAfter(response);
  return new ApiError(response.status, detail, retryAfter);
}
