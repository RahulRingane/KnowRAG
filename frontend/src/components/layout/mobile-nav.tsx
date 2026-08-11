"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Menu } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { NAV_LINKS } from "@/components/layout/nav-links";

/** Small-screen navigation — a menu button that opens the same links `MainNav` renders. */
export function MobileNav() {
  const pathname = usePathname();

  return (
    <div className="md:hidden">
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button variant="ghost" size="icon" aria-label="Open navigation menu">
              <Menu className="size-5" />
            </Button>
          }
        />
        <DropdownMenuContent align="end">
          {NAV_LINKS.map((link) => {
            const isActive = link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
            return (
              <DropdownMenuItem
                key={link.href}
                render={
                  <Link
                    href={link.href}
                    aria-current={isActive ? "page" : undefined}
                    className={cn(isActive && "font-medium")}
                  >
                    {link.label}
                  </Link>
                }
              />
            );
          })}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
