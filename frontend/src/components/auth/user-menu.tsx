"use client";

import Link from "next/link";
import { LogOut, User } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth, useLogout } from "@/lib/hooks/useAuth";

/**
 * Auth-aware corner of the header, following the same client-island
 * pattern `Header` already uses for `ThemeToggle`/`MobileNav`: `Header`
 * stays a server component and this is the piece that needs `useAuth`'s
 * hooks.
 *
 * Logging out doesn't navigate anywhere from here on purpose — clearing the
 * token flips `useIsAuthenticated()`, which flips `AuthProvider`'s boot
 * status to `"unauthenticated"`, which is exactly what `RouteGuard` already
 * watches for on every `(app)` route. One state change, one place that
 * reacts to it, instead of this component also owning a redirect.
 */
export function UserMenu() {
  const { isAuthenticated, user, isLoading } = useAuth();
  const logout = useLogout();

  if (!isAuthenticated) {
    return <Button variant="outline" size="sm" render={<Link href="/login">Sign in</Link>} />;
  }

  if (isLoading) {
    return <Skeleton className="size-8 rounded-full" aria-hidden="true" />;
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            variant="ghost"
            size="icon"
            aria-label={user ? `Account menu for ${user.username}` : "Account menu"}
          >
            <User className="size-4" />
          </Button>
        }
      />
      <DropdownMenuContent align="end">
        <DropdownMenuLabel>{user?.username ?? "Account"}</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          variant="destructive"
          disabled={logout.isPending}
          onClick={() => logout.mutate()}
        >
          <LogOut />
          {logout.isPending ? "Signing out…" : "Sign out"}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
