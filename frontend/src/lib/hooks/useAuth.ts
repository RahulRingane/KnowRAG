"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSyncExternalStore } from "react";

import { login, logout, me, register } from "@/lib/api/endpoints";
import { clearAccessToken, getAccessToken, setAccessToken, subscribeToTokenChanges } from "@/lib/auth/token-store";
import type { AuthUser, Credentials, TokenPair } from "@/types/api";

import { currentUserKey } from "./query-keys";

/**
 * Subscribes to the in-memory token store (`lib/auth/token-store.ts`) via
 * `useSyncExternalStore` so React re-renders on login/logout/refresh
 * failure without polling. `getServerSnapshot` always returns `false`: the
 * token store is a plain module-level variable, never populated during
 * SSR, so there is nothing to hydrate from the server — the client
 * re-evaluates once `bootstrapSession()` (see `lib/auth/refresh.ts`) runs
 * in an effect, which is WS-C's `AuthProvider`'s job, not this hook's.
 */
export function useIsAuthenticated(): boolean {
  return useSyncExternalStore(
    subscribeToTokenChanges,
    () => getAccessToken() !== null,
    () => false,
  );
}

/** `GET /auth/me` for the current session. `enabled` on
 *  `useIsAuthenticated()` so this never fires — and never draws a spurious
 *  401 — before a token exists, e.g. during the boot-time refresh window
 *  `AuthProvider` runs before guarded content first renders. */
export function useCurrentUser() {
  const isAuthenticated = useIsAuthenticated();
  return useQuery<AuthUser>({
    queryKey: currentUserKey(),
    queryFn: () => me(),
    enabled: isAuthenticated,
    retry: false,
  });
}

/** Convenience composite of the two hooks above, for call sites (nav/user
 *  menu, route guards) that just want "am I logged in, and as whom". */
export function useAuth() {
  const isAuthenticated = useIsAuthenticated();
  const currentUser = useCurrentUser();
  return {
    isAuthenticated,
    user: currentUser.data ?? null,
    isLoading: isAuthenticated && currentUser.isLoading,
  };
}

/**
 * `POST /auth/login`. Writes the access token into the module-level store
 * on success — done here, in the hook layer, rather than inside
 * `endpoints.login()`, so `lib/api/endpoints.ts` stays pure HTTP transport
 * with zero knowledge of session state (§ conventions: services/transport
 * modules don't reach into unrelated state).
 */
export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation<TokenPair, Error, Credentials>({
    mutationFn: (credentials) => login(credentials),
    onSuccess: (tokenPair) => {
      setAccessToken(tokenPair.access_token);
      void queryClient.invalidateQueries({ queryKey: currentUserKey() });
    },
  });
}

/** `POST /auth/register`. A `403` ("Registration is closed…") surfaces as
 *  a rejected `ApiError` on the mutation for the caller to render — no
 *  special-casing here, since the UI needs the exact detail message. */
export function useRegister() {
  return useMutation<AuthUser, Error, Credentials>({
    mutationFn: (credentials) => register(credentials),
  });
}

/**
 * `POST /auth/logout`. Clears the local token store in `onSettled`, not
 * only `onSuccess`: the user's intent is to end the session locally
 * regardless of whether the network call itself succeeds (e.g. they're
 * already offline, or the access token had already expired) — the
 * server-side `token_version` revocation is best-effort from the client's
 * point of view, but the in-memory token disappearing (and every guarded
 * view reacting to it) is what the UI actually depends on.
 */
export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, void>({
    mutationFn: () => logout(),
    onSettled: () => {
      clearAccessToken();
      queryClient.removeQueries({ queryKey: currentUserKey() });
    },
  });
}
