"use client";

import { useEffect, useRef } from "react";
import { Dialog as DialogPrimitive } from "@base-ui/react/dialog";
import { FileWarning, XIcon } from "lucide-react";

import { EmptyState } from "@/components/layout/empty-state";
import { ErrorState } from "@/components/layout/error-state";
import { TextSkeleton } from "@/components/layout/skeletons";
import { Button } from "@/components/ui/button";
import { useChunks } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import { formatChunkKey } from "@/lib/utils/chunk-key";

export interface EvidencePanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Usually `retrieved_chunk_ids` from the active result — everything a
   *  citation chip could point at. */
  chunkIds: string[];
  /** The chunk a citation chip was just clicked for — scrolled into view
   *  and given focus when the panel opens, per the "opens/focuses the
   *  evidence panel on the corresponding chunk" requirement. `null` means
   *  "just show the list", e.g. opened some other way. */
  focusedChunkId: string | null;
}

/**
 * Real chunk text for `retrieved_chunk_ids`, via `useChunks` (WS-B). Built
 * directly on `@base-ui/react/dialog` rather than the shared
 * `components/ui/dialog.tsx` — that file's `DialogContent` bakes in a
 * centred-modal position (`top-1/2 left-1/2 -translate-x-1/2 ...`), and this
 * needs a genuinely different layout (§ "What to build": a bottom drawer
 * below `md`, a right-side panel at `md` and up), not a restyled centred
 * dialog. Reusing `Dialog`/`DialogTrigger`/`DialogClose` here would still
 * inherit that positioning through `DialogPopup`'s base classes fighting
 * this component's own via `twMerge`, which is more fragile than composing
 * the primitive directly — `ui/dialog.tsx` is left untouched either way.
 *
 * Every retrieved chunk is listed, not just the one that was clicked,
 * because the panel is meant to be browsable (jumping from citation to
 * citation without re-opening), not a single-chunk lookup.
 */
export function EvidencePanel({ open, onOpenChange, chunkIds, focusedChunkId }: EvidencePanelProps) {
  const { data, isLoading, isError, refetch } = useChunks(chunkIds);
  const itemRefs = useRef(new Map<string, HTMLDivElement | null>());

  useEffect(() => {
    if (!open || !focusedChunkId) return;
    const el = itemRefs.current.get(focusedChunkId);
    el?.scrollIntoView({ block: "nearest" });
    el?.focus();
  }, [open, focusedChunkId, data]);

  // `useChunks` returns only the chunks that were FOUND — an id with no
  // match simply has no entry (plan §1.4), not a null placeholder — so a
  // lookup miss below is read as "text unavailable", never as an error.
  const textByKey = new Map<string, string>();
  for (const chunk of data ?? []) {
    textByKey.set(formatChunkKey(chunk.document_id, chunk.chunk_index), chunk.text);
  }

  return (
    <DialogPrimitive.Root open={open} onOpenChange={(next) => onOpenChange(next)}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Backdrop className="data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0 fixed inset-0 z-50 bg-black/20 duration-100" />
        <DialogPrimitive.Popup
          className={cn(
            "bg-popover text-popover-foreground ring-foreground/10 fixed z-50 flex flex-col gap-3 p-4 text-sm shadow-lg ring-1 outline-none",
            "data-open:animate-in data-closed:animate-out duration-150",
            // Below `md`: a bottom drawer. At `md` and up: a right-side panel.
            "inset-x-0 bottom-0 max-h-[75vh] rounded-t-xl border-t",
            "md:inset-y-0 md:top-0 md:right-0 md:bottom-0 md:left-auto md:h-full md:max-h-none md:w-full md:max-w-md md:rounded-none md:rounded-l-xl md:border-t-0 md:border-l",
          )}
        >
          <div className="flex items-center justify-between gap-2">
            <DialogPrimitive.Title className="font-heading text-base font-medium">
              Evidence
            </DialogPrimitive.Title>
            <DialogPrimitive.Close render={<Button variant="ghost" size="icon-sm" />}>
              <XIcon />
              <span className="sr-only">Close</span>
            </DialogPrimitive.Close>
          </div>
          <DialogPrimitive.Description className="text-muted-foreground -mt-2 text-xs">
            Retrieved chunk text, as considered during this query.
          </DialogPrimitive.Description>

          <div className="min-h-0 flex-1 space-y-3 overflow-y-auto">
            {chunkIds.length === 0 ? (
              <EmptyState
                title="No evidence yet"
                description="Nothing has been retrieved for this query."
              />
            ) : isLoading ? (
              <TextSkeleton lines={6} />
            ) : isError ? (
              <ErrorState message="Couldn't load evidence text." onRetry={() => void refetch()} />
            ) : (data?.length ?? 0) === 0 ? (
              <EmptyState
                title="No evidence found"
                description="None of the retrieved chunk ids resolved to stored text."
              />
            ) : (
              chunkIds.map((chunkId) => {
                const text = textByKey.get(chunkId);
                return (
                  <div
                    key={chunkId}
                    ref={(el) => {
                      itemRefs.current.set(chunkId, el);
                    }}
                    tabIndex={-1}
                    className={cn(
                      "rounded-lg border p-2.5 outline-none",
                      chunkId === focusedChunkId
                        ? "border-ring ring-ring/50 ring-2"
                        : "border-border",
                    )}
                  >
                    <p className="text-muted-foreground mb-1 font-mono text-xs">{chunkId}</p>
                    {text !== undefined ? (
                      <p className="whitespace-pre-wrap">{text}</p>
                    ) : (
                      <p className="text-muted-foreground flex items-center gap-1 italic">
                        <FileWarning className="size-3.5" aria-hidden="true" />
                        text unavailable
                      </p>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
