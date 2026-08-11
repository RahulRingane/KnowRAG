import { RouteGuard } from "@/components/auth/route-guard";
import { AppShell } from "@/components/layout/app-shell";

/**
 * Layout for every authenticated app route (`/`, `/documents`, `/history`,
 * `/status`). Supplies the header/nav and the app's single `<main>`
 * landmark via `AppShell`, wrapped in `RouteGuard` — a client island, so
 * this layout itself stays a server component. `RouteGuard` renders a
 * loading placeholder while the boot-time refresh is still in flight,
 * `AppShell` + `children` once a session is confirmed, and redirects to
 * `/login?next=…` otherwise; see `RouteGuard`'s own comment for why it
 * must never redirect during the "still checking" window.
 */
export default function AppRouteGroupLayout({ children }: { children: React.ReactNode }) {
  return (
    <RouteGuard>
      <AppShell>{children}</AppShell>
    </RouteGuard>
  );
}
