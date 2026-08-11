import type { LucideIcon } from "lucide-react";
import { Inbox } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export interface EmptyStateProps {
  title: string;
  description?: string;
  icon?: LucideIcon;
  /** e.g. an "Upload a document" button. Optional. */
  action?: ReactNode;
  className?: string;
}

/**
 * The shared "empty" state of the idle/loading/error/empty rule (§5.3).
 * Use when a request succeeded but returned nothing to show — an empty
 * document list, no history yet, zero claims — which is a distinct case
 * from "error" and must not be rendered as one.
 */
export function EmptyState({
  title,
  description,
  icon: Icon = Inbox,
  action,
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        "border-border flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed px-6 py-12 text-center",
        className,
      )}
    >
      <Icon className="text-muted-foreground mb-2 size-8" aria-hidden="true" />
      <p className="text-sm font-medium">{title}</p>
      {description ? <p className="text-muted-foreground max-w-sm text-sm">{description}</p> : null}
      {action ? <div className="mt-3">{action}</div> : null}
    </div>
  );
}
