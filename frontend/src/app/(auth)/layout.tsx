import Link from "next/link";
import { ShieldCheck } from "lucide-react";

/**
 * Minimal shell for `(auth)/*` routes — deliberately not `AppShell`. An
 * unauthenticated visitor has no app nav to show (every `(app)` link would
 * just send `RouteGuard` right back here), and `AppShell`'s own comment
 * says it must stay the ONLY place `<main>` renders for the `(app)` group
 * — so this layout supplies the app's *other* `<main>` landmark, centered,
 * with just the brand mark. No `Header`, no `UserMenu`: nothing here
 * depends on auth state, so this layout stays a server component.
 */
export default function AuthRouteGroupLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-svh flex-col items-center justify-center gap-8 px-4 py-12">
      <Link
        href="/"
        className="focus-visible:ring-ring flex items-center gap-2 rounded-md text-sm font-semibold focus-visible:ring-2 focus-visible:outline-none"
      >
        <ShieldCheck className="text-primary size-5" aria-hidden="true" />
        <span>KnowRAG</span>
      </Link>
      <main id="main-content" className="w-full max-w-sm">
        {children}
      </main>
    </div>
  );
}
