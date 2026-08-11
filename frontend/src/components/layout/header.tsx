import Link from "next/link";
import { ShieldCheck } from "lucide-react";

import { UserMenu } from "@/components/auth/user-menu";
import { MainNav } from "@/components/layout/main-nav";
import { MobileNav } from "@/components/layout/mobile-nav";
import { ThemeToggle } from "@/components/layout/theme-toggle";

/**
 * App header. This component is a server component;
 * `MainNav`/`MobileNav`/`ThemeToggle`/`UserMenu` are the client islands
 * inside it. `Header` only ever renders on `(app)` routes once `RouteGuard`
 * has confirmed a session, so `UserMenu` in practice always has one — it
 * still handles the logged-out case itself rather than assuming that.
 */
export function Header() {
  return (
    <header className="border-border bg-background/95 supports-[backdrop-filter]:bg-background/60 sticky top-0 z-40 border-b backdrop-blur">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-4 px-4">
        <Link
          href="/"
          className="focus-visible:ring-ring flex items-center gap-2 rounded-md text-sm font-semibold focus-visible:ring-2 focus-visible:outline-none"
        >
          <ShieldCheck className="text-primary size-5" aria-hidden="true" />
          <span>KnowRAG</span>
        </Link>

        <MainNav />

        <div className="flex items-center gap-1">
          <ThemeToggle />
          <UserMenu />
          <MobileNav />
        </div>
      </div>
    </header>
  );
}
