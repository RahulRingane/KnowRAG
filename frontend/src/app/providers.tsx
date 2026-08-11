"use client";

import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider as NextThemesProvider } from "next-themes";

import { AuthProvider } from "@/components/auth/auth-provider";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/sonner";

/**
 * App-wide providers, nested in this order:
 *   ThemeProvider -> QueryClientProvider -> AuthProvider -> TooltipProvider -> {children}
 * plus the toast portal rendered as a sibling.
 *
 * `AuthProvider` sits inside `QueryClientProvider` because its boot-time
 * refresh (`bootstrapSession()`) shares the same single-flight machinery
 * TanStack Query's own 401 retries use, and it subscribes to
 * `useIsAuthenticated()`, a hook built on `useSyncExternalStore` — neither
 * needs a `QueryClient` directly, but keeping it inside the provider that
 * every data-fetching hook already depends on avoids ever having to reason
 * about which one mounts first.
 *
 * `QueryClient` is created inside `useState` so it survives re-renders but
 * is never shared across requests during SSR (a module-level client would
 * leak data between users on the server).
 *
 * Defaults are tuned for a backend that answers in 2.7-7.4s warm and up to
 * ~25s cold (see CLAUDE.md "Latency and the timing breakdown"): no
 * refetch-on-window-focus (a slow, sometimes-billed LLM call should not
 * silently re-fire because a tab regained focus), a single retry (enough to
 * absorb a transient blip without tripling an already-slow request), and a
 * 30s staleTime so switching between routes doesn't re-trigger an expensive
 * fetch that just resolved. Individual queries (e.g. `/health` polling,
 * `/ingest/{id}` status polling) override this per-query in WS-B's hooks.
 */
export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = React.useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            refetchOnWindowFocus: false,
            retry: 1,
            staleTime: 30_000,
          },
          mutations: {
            retry: 0,
          },
        },
      }),
  );

  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange
    >
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <TooltipProvider>
            {children}
            <Toaster />
          </TooltipProvider>
        </AuthProvider>
      </QueryClientProvider>
    </NextThemesProvider>
  );
}
