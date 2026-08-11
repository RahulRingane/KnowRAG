/**
 * Holds the access JWT in a module-level variable — deliberately NOT
 * `localStorage`, `sessionStorage`, or a script-readable cookie. That is
 * the entire point of this pattern (plan §3.2): a token that only ever
 * lives in JS memory can't be exfiltrated by an XSS payload reading
 * storage, and it evaporates on tab close/reload by construction — which
 * is exactly why `bootstrapSession()` in `./refresh.ts` exists, to
 * re-establish it from the httpOnly refresh cookie on every fresh load.
 *
 * A module-level variable is shared by every importer within the same JS
 * realm (one browser tab, one module graph), which is exactly what's
 * wanted: one in-memory session per tab. It is never written during SSR —
 * there is no request-scoped wrapper around it — so this module must never
 * be used to decide what a server-rendered page shows; only client
 * components reading it after mount are safe.
 */

type Listener = () => void;

let accessToken: string | null = null;
const listeners = new Set<Listener>();

/** Current access token, or `null` if there is no active session in memory. */
export function getAccessToken(): string | null {
  return accessToken;
}

/** Stores a freshly issued or refreshed access token and notifies subscribers. */
export function setAccessToken(token: string): void {
  accessToken = token;
  notify();
}

/**
 * Drops the in-memory token (logout, refresh failure, boot with no
 * session) and notifies subscribers. No-ops without notifying if there was
 * nothing to clear, so an app-start "not logged in" doesn't fire a spurious
 * re-render of every subscriber before they've even mounted.
 */
export function clearAccessToken(): void {
  if (accessToken === null) return;
  accessToken = null;
  notify();
}

/**
 * Lets React (or anything else) react to login/logout/refresh-failure
 * without polling `getAccessToken()`. Returns an unsubscribe function,
 * matching the shape `useSyncExternalStore` expects so `lib/hooks/useAuth.ts`
 * can wire this store up directly as its subscription source.
 */
export function subscribeToTokenChanges(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function notify(): void {
  for (const listener of listeners) listener();
}
