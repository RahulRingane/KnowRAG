import { env } from "@/config/env";
import { TokenPairSchema } from "@/lib/schemas/auth";

import { clearAccessToken, setAccessToken } from "./token-store";

/**
 * Single-flight, silent-refresh interceptor. Read this file before touching
 * any auth code (plan §3.2) — this is the single most defect-prone piece
 * of the frontend.
 *
 * The problem it solves: the refresh token is HttpOnly, `Path=/auth`, and
 * rotates on every use (backend §3.1). If N in-flight requests each
 * independently see a `401` and each fire their own `POST /auth/refresh`,
 * the FIRST response rotates the cookie and every other refresh call
 * arrives holding an already-invalidated token, gets rejected, and clears
 * the session — the user is logged out nondeterministically, depending on
 * network timing, even though their session was perfectly valid a moment
 * earlier. This is exactly the failure the plan calls out by name.
 *
 * The fix: every caller within one refresh window awaits the *same*
 * Promise. `inFlightRefresh` is a plain module-level variable — not React
 * state, not a class field — specifically so it's shared across every
 * import of this module, which is what lets concurrent callers from
 * unrelated call sites (the `client.ts` 401 interceptor, `sse.ts`'s
 * pre-stream check, `bootstrapSession()`) collapse onto one network call.
 * It's cleared in a `finally` so a later, genuinely-expired token can still
 * trigger a fresh refresh.
 */

let inFlightRefresh: Promise<string | null> | null = null;

/**
 * Endpoints that must never be routed through the refresh interceptor. A
 * `401` from `/auth/login` or `/auth/register` is the true answer ("wrong
 * password", "already registered"), not an expired-access-token signal —
 * refreshing and retrying either would just repeat the same failure with
 * extra steps. A `401` from `/auth/refresh` itself means the refresh
 * cookie is missing, expired, or revoked; routing that back into
 * `refreshAccessToken()` would recurse. `startsWith` (not equality) so
 * this also covers any future sub-path under `/auth/refresh` or similar.
 */
const REFRESH_EXEMPT_PREFIXES = ["/auth/login", "/auth/register", "/auth/refresh"];

/** True if `path` must be excluded from the 401→refresh→retry interceptor. */
export function isExemptFromRefresh(path: string): boolean {
  return REFRESH_EXEMPT_PREFIXES.some((prefix) => path.startsWith(prefix));
}

async function performRefresh(): Promise<string | null> {
  try {
    const response = await fetch(new URL("/auth/refresh", env.apiBaseUrl), {
      method: "POST",
      // The refresh cookie is HttpOnly + Path=/auth: this is the only way
      // the browser will attach it (and the only way a rotated Set-Cookie
      // on the response gets stored back), since the API origin differs
      // from the app origin in local dev (localhost:3000 vs :8000/:8001).
      credentials: "include",
      headers: { Accept: "application/json" },
    });

    if (!response.ok) {
      clearAccessToken();
      return null;
    }

    const parsed = TokenPairSchema.safeParse(await response.json());
    if (!parsed.success) {
      clearAccessToken();
      return null;
    }

    setAccessToken(parsed.data.access_token);
    return parsed.data.access_token;
  } catch {
    // Network failure, offline, CORS misconfiguration, etc. Treated the
    // same as "no valid session": fail closed rather than leave a stale
    // token in memory that will just 401 again on the very next call.
    clearAccessToken();
    return null;
  }
}

/**
 * Ensures at most one `/auth/refresh` request is ever in flight at a time.
 * Every caller that needs a fresh access token — the 401 interceptor in
 * `lib/api/client.ts`, the pre-stream check in `lib/api/sse.ts`, and
 * `bootstrapSession()` below — calls this exact function, so N concurrent
 * callers within one refresh window share one Promise, therefore one
 * network call and one cookie rotation. Resolves to `null` (never throws)
 * on any failure — callers branch on the return value, not a catch block.
 */
export function refreshAccessToken(): Promise<string | null> {
  if (inFlightRefresh) return inFlightRefresh;
  inFlightRefresh = performRefresh().finally(() => {
    inFlightRefresh = null;
  });
  return inFlightRefresh;
}

/**
 * Called once at app start, before any guarded content renders, to turn a
 * still-valid httpOnly refresh cookie into an in-memory access token —
 * without this, every page reload would look like a logout even though
 * the cookie (7-day TTL) is still good. Resolves to `false` rather than
 * throwing when there's no session to restore (no cookie, expired,
 * revoked): "not logged in" is an expected, common outcome on first paint,
 * not an error to be caught and logged. Callers (WS-C's `AuthProvider`)
 * should await this before deciding whether to render guarded content or
 * redirect to `/login`.
 */
export async function bootstrapSession(): Promise<boolean> {
  const token = await refreshAccessToken();
  return token !== null;
}
