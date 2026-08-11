import { FileText } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * A single `[Cn]` citation tag rendered as an interactive chip. §5.3's a11y
 * floor requires citation chips to be real, keyboard-reachable buttons with
 * an accessible name that NAMES THE CHUNK — not just "C1", which means
 * nothing to a screen reader user who can't see the superscript styling
 * that would otherwise carry the meaning.
 *
 * `chunkId` is `null` when the tag couldn't be resolved to a chunk (trap #4
 * — see `citation-resolution.ts`): rather than a disabled button (which
 * drops out of the tab order and reads as "this control is broken"), an
 * unresolved tag renders as a plain, non-interactive span so it's still
 * visible as a citation marker without pretending it can do something it
 * can't.
 */
export function CitationChip({
  tag,
  chunkId,
  onOpen,
}: {
  tag: string;
  chunkId: string | null;
  onOpen: (chunkId: string) => void;
}) {
  if (chunkId === null) {
    return (
      <span
        className="text-muted-foreground bg-muted mx-0.5 inline-flex h-5 items-center rounded px-1.5 align-baseline text-xs font-medium"
        title="This citation didn't resolve to a retrieved chunk."
      >
        [{tag}]
      </span>
    );
  }

  return (
    <button
      type="button"
      onClick={() => onOpen(chunkId)}
      aria-label={`View evidence for citation ${tag}, chunk ${chunkId}`}
      className={cn(
        "border-border bg-secondary text-secondary-foreground hover:bg-primary hover:text-primary-foreground",
        "focus-visible:ring-ring/50 focus-visible:border-ring mx-0.5 inline-flex h-5 items-center gap-0.5 rounded border px-1.5 align-baseline text-xs font-medium transition-colors outline-none focus-visible:ring-3",
      )}
    >
      <FileText className="size-3" aria-hidden="true" />[{tag}]
    </button>
  );
}
