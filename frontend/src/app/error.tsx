"use client";

import { useEffect } from "react";
import { AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui/button";

/**
 * Next.js route-segment error boundary. Catches render errors thrown below
 * this segment; §5.3's error state and this file serve different failure
 * modes — ErrorState/error-in-a-query-hook is an expected API failure,
 * this is an unexpected render crash.
 */
export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Last-resort visibility until an error tracker is wired up.
    console.error(error);
  }, [error]);

  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 px-4 text-center">
      <AlertTriangle className="text-destructive size-10" aria-hidden="true" />
      <div className="space-y-1">
        <h1 className="text-xl font-semibold">Something went wrong</h1>
        <p className="text-muted-foreground max-w-md text-sm">
          {error.message || "An unexpected error occurred while rendering this page."}
        </p>
      </div>
      <Button onClick={reset}>Try again</Button>
    </div>
  );
}
