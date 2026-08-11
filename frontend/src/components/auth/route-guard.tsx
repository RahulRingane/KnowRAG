"use client";

import * as React from "react";
import { usePathname, useRouter } from "next/navigation";

import { useAuthBootStatus } from "@/components/auth/auth-provider";
import { buildLoginHref } from "@/components/auth/redirect";
import { TextSkeleton } from "@/components/layout/skeletons";

/**
 * Route guard for the whole `(app)` route group. `(app)/layout.tsx` stays a
 * server component and wraps `<AppShell>` in this client island rather than
 * becoming a client component itself — the guard is the only piece that
 * needs `usePathname()`/`useRouter()`/the boot-status context.
 *
 * The bug this exists to avoid: redirecting while `useAuthBootStatus()` is
 * still `"pending"` bounces every page reload to `/login`, because there is
 * briefly no token in memory even on a perfectly valid session (the
 * boot-time silent refresh in `AuthProvider` hasn't resolved yet). So the
 * effect below only ever fires on `"unauthenticated"` — a status
 * `AuthProvider` doesn't reach until the one boot-time refresh attempt has
 * already come back empty.
 */
export function RouteGuard({ children }: { children: React.ReactNode }) {
  const status = useAuthBootStatus();
  const pathname = usePathname();
  const router = useRouter();

  React.useEffect(() => {
    if (status !== "unauthenticated") return;
    router.replace(buildLoginHref(pathname));
  }, [status, pathname, router]);

  if (status === "authenticated") {
    return <>{children}</>;
  }

  // "pending" (still checking) and "unauthenticated" (redirect just
  // scheduled above, navigation hasn't landed yet) render the same loading
  // placeholder rather than `null` or the guarded content — a blank frame
  // between "no session found" and "browser actually on /login" would
  // otherwise flash empty for a tick, and `null` reads to a screen reader
  // as nothing happening rather than "checking".
  return (
    <div
      className="mx-auto flex min-h-svh w-full max-w-6xl flex-col gap-6 px-4 py-8"
      role="status"
      aria-live="polite"
    >
      <span className="sr-only">
        {status === "pending" ? "Checking your session…" : "Redirecting to sign in…"}
      </span>
      <TextSkeleton lines={4} />
    </div>
  );
}
