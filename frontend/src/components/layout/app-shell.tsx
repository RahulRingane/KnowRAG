import type { ReactNode } from "react";

import { Header } from "@/components/layout/header";

/**
 * The single `<main>` landmark for authenticated app routes. Root
 * `layout.tsx` only supplies `<html>`/`<body>`; auth pages under
 * `(auth)/` get their own minimal shell (WS-C) without this nav, so this
 * component must stay the ONLY place `<main>` is rendered for `(app)/*`.
 */
export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-svh flex-col">
      <Header />
      <main id="main-content" className="mx-auto w-full max-w-6xl flex-1 px-4 py-8">
        {children}
      </main>
    </div>
  );
}
