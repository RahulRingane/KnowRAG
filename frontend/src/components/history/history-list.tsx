"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { History as HistoryIcon, RotateCcw, Trash2 } from "lucide-react";

import { EmptyState } from "@/components/layout/empty-state";
import { ListSkeleton } from "@/components/layout/skeletons";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { clearHistory, readHistory, removeHistoryEntry, type HistoryEntry } from "@/lib/storage/history";
import { formatTimestamp } from "@/lib/utils/format";

import { HistoryStateBadge } from "./history-state-badge";

/**
 * Re-run navigates to `/` with the question prefilled via the `q` query
 * string param (frontend_plan.md WS-E: "a query-string param is fine").
 * The query composer (WS-D) is expected to read `useSearchParams().get("q")`
 * on mount and seed its textarea with it — that wiring happens on WS-D's
 * side at integration time, same as `addHistoryEntry` (see
 * `lib/storage/history.ts`'s header comment).
 *
 * Exported (integration-time addition, WS-D) so the query page can import
 * this exact constant instead of retyping the string "q" — two independent
 * literals would silently drift if this ever changed.
 */
export const RERUN_QUERY_PARAM = "q";

/**
 * Renders localStorage-backed query history (`lib/storage/history.ts`).
 * Hydrated in an effect, never read at module scope or during the first
 * render — `localStorage` doesn't exist on the server, and reading it
 * during render would also make the server-rendered HTML disagree with
 * the client's first paint. `mounted` gates that: a skeleton renders for
 * both the server pass and the client's first paint, then the real list
 * replaces it once the effect has actually read storage.
 */
export function HistoryList() {
  const [mounted, setMounted] = useState(false);
  const [entries, setEntries] = useState<HistoryEntry[]>([]);

  useEffect(() => {
    setEntries(readHistory());
    setMounted(true);
  }, []);

  function handleRemove(id: string) {
    setEntries(removeHistoryEntry(id));
  }

  function handleClearAll() {
    clearHistory();
    setEntries([]);
  }

  if (!mounted) {
    return <ListSkeleton rows={3} />;
  }

  if (entries.length === 0) {
    return (
      <EmptyState
        icon={HistoryIcon}
        title="No query history yet"
        description="Questions and statements you run show up here so you can re-run or review them later."
      />
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <Dialog>
          <DialogTrigger render={<Button variant="outline" size="sm">Clear all</Button>} />
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Clear all history?</DialogTitle>
              <DialogDescription>
                This removes all {entries.length} stored {entries.length === 1 ? "entry" : "entries"}{" "}
                from this browser. It can&apos;t be undone.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <DialogClose render={<Button variant="outline">Cancel</Button>} />
              <DialogClose
                render={
                  <Button variant="destructive" onClick={handleClearAll}>
                    Clear all
                  </Button>
                }
              />
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      <ul className="divide-border border-border divide-y overflow-hidden rounded-lg border">
        {entries.map((entry) => (
          <li key={entry.id} className="flex flex-wrap items-start justify-between gap-3 px-4 py-3">
            <div className="min-w-0 space-y-1">
              <p className="text-sm font-medium break-words">{entry.question}</p>
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="outline">{entry.inputType === "fact" ? "Fact check" : "Question"}</Badge>
                <HistoryStateBadge state={entry.state} />
                <span className="text-muted-foreground text-xs">{formatTimestamp(entry.ranAt)}</span>
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={`Re-run "${entry.question}"`}
                render={
                  <Link href={{ pathname: "/", query: { [RERUN_QUERY_PARAM]: entry.question } }} />
                }
              >
                <RotateCcw />
              </Button>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={`Delete "${entry.question}" from history`}
                onClick={() => handleRemove(entry.id)}
              >
                <Trash2 />
              </Button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
