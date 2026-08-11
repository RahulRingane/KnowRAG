"use client";

import * as React from "react";

import { useIsAuthenticated } from "@/lib/hooks/useAuth";
import { bootstrapSession } from "@/lib/auth/refresh";

/**
 * Boot-time session status, distinct from "am I logged in right now"
 * (`useIsAuthenticated`, `lib/hooks/useAuth.ts`) in exactly one way: it
 * distinguishes "haven't checked yet" from "checked and there's no
 * session". A route guard that can't see that distinction has only one
 * lever — redirect or don't — and during the boot-time silent refresh
 * (`bootstrapSession()`, `lib/auth/refresh.ts`) there is no token in
 * memory yet even when the httpOnly refresh cookie is about to prove the
 * session is still good. Redirecting on "no token yet" bounces every page
 * reload to `/login` before the refresh had a chance to run — that's the
 * specific bug this type exists to make impossible to express.
 */
export type AuthBootStatus = "pending" | "authenticated" | "unauthenticated";

const AuthBootStatusContext = React.createContext<AuthBootStatus>("pending");

/** `(app)/layout.tsx`'s `RouteGuard` (and anything else that needs to tell
 *  "still checking" apart from "definitely logged out") reads this instead
 *  of `useIsAuthenticated()` directly. */
export function useAuthBootStatus(): AuthBootStatus {
  return React.useContext(AuthBootStatusContext);
}

/**
 * Runs the one-time boot-time silent refresh (plan §3.2) before any guarded
 * content renders, then hands off to the live token-store subscription for
 * everything after that — so a login or logout happening later in the
 * session is reflected immediately without this provider needing its own
 * copy of "am I authenticated".
 *
 * Must run in an effect, not during render: `bootstrapSession()` calls
 * `fetch()` (via `refreshAccessToken()`), which doesn't exist during SSR,
 * and even if it did, the in-memory token store (`lib/auth/token-store.ts`)
 * is never populated server-side by design — there is nothing to check
 * until this runs on the client after mount.
 *
 * `bootedRef` makes the bootstrap call idempotent under React 19
 * StrictMode's dev-mode mount→cleanup→mount double-invocation: without it,
 * two `bootstrapSession()` calls would still collapse onto one network
 * request (it delegates to `refreshAccessToken()`'s own single-flight
 * guard), but the ref keeps this component from even trying, which is
 * simpler to reason about and doesn't depend on that lower-level guard's
 * timing.
 */
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [booted, setBooted] = React.useState(false);
  const bootedRef = React.useRef(false);

  React.useEffect(() => {
    if (bootedRef.current) return;
    bootedRef.current = true;

    let cancelled = false;
    void bootstrapSession().finally(() => {
      // `bootstrapSession()` never rejects (see its own doc comment), so
      // there is no error branch here — only "did this specific effect
      // instance's caller stick around long enough to care".
      if (!cancelled) setBooted(true);
    });

    return () => {
      cancelled = true;
    };
  }, []);

  const isAuthenticated = useIsAuthenticated();
  const status: AuthBootStatus = !booted
    ? "pending"
    : isAuthenticated
      ? "authenticated"
      : "unauthenticated";

  return (
    <AuthBootStatusContext.Provider value={status}>{children}</AuthBootStatusContext.Provider>
  );
}
