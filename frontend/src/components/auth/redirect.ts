/**
 * Shared `?next=` handling for the login form (`login-form.tsx`) and the
 * route guard (`route-guard.tsx`) so the two agree on what a safe redirect
 * target looks like — logic like this drifting into two independent
 * copies is exactly how one of them ends up accepting something the other
 * rejects.
 *
 * Only a same-origin path starting with a single `/` is accepted. Rejecting
 * anything else matters: `?next=` is attacker-controlled (it's a URL query
 * param), so a login link crafted as `/login?next=https://evil.example` or
 * `/login?next=//evil.example` (protocol-relative — still leaves the
 * origin) must not be able to send a freshly-authenticated user off-app the
 * moment they sign in.
 */

const DEFAULT_REDIRECT = "/";

/** True when `target` is safe to hand to `router.replace()` directly: a
 *  same-origin path. Doubles as a type guard so callers get `string`, not
 *  `string | null`, on the success branch. */
export function isSafeRedirectTarget(target: string | null | undefined): target is string {
  if (!target) return false;
  if (!target.startsWith("/")) return false;
  // `//host/path` and `/\host/path` are both protocol-relative in at least
  // one major browser's URL parser despite starting with `/` — either would
  // still navigate off-origin.
  if (target.startsWith("//") || target.startsWith("/\\")) return false;
  return true;
}

/** Builds the href `RouteGuard` sends an unauthenticated visitor to,
 *  preserving where they were headed so `resolveNextParam` can send them
 *  back after login. Omits `?next=` for the app's own default route so a
 *  plain, un-redirected visit to `/login` doesn't grow a redundant
 *  `?next=%2F`. */
export function buildLoginHref(currentPath: string): string {
  if (!isSafeRedirectTarget(currentPath) || currentPath === DEFAULT_REDIRECT) {
    return "/login";
  }
  return `/login?next=${encodeURIComponent(currentPath)}`;
}

/** Reads and validates `next` from the login page's search params for the
 *  post-login redirect. A missing, malformed, or unsafe value degrades to
 *  "go home" rather than throwing — a tampered or stale query string should
 *  never break the login flow itself. */
export function resolveNextParam(searchParams: URLSearchParams): string {
  const next = searchParams.get("next");
  return isSafeRedirectTarget(next) ? next : DEFAULT_REDIRECT;
}
