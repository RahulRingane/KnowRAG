import { AlertTriangle } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

export interface ErrorStateProps {
  /** Short, user-facing summary. Keep vendor/stack detail out of this string. */
  message: string;
  /** Optional longer detail (e.g. server-provided detail text). */
  detail?: string;
  /** Omit to render without a retry action (e.g. a terminal error). */
  onRetry?: () => void;
  retryLabel?: string;
  className?: string;
}

/**
 * The shared "error" state of the idle/loading/error/empty rule (§5.3).
 * Every API-touching component renders this — never a bare thrown error or
 * a blank screen — when its request fails.
 */
export function ErrorState({
  message,
  detail,
  onRetry,
  retryLabel = "Retry",
  className,
}: ErrorStateProps) {
  return (
    <Alert variant="destructive" className={className}>
      <AlertTriangle className="size-4" />
      <AlertTitle>{message}</AlertTitle>
      {detail ? <AlertDescription>{detail}</AlertDescription> : null}
      {onRetry ? (
        <Button variant="outline" size="sm" onClick={onRetry} className="mt-3">
          {retryLabel}
        </Button>
      ) : null}
    </Alert>
  );
}
