import { HistoryList } from "@/components/history/history-list";

/**
 * Server component delegating entirely to the `HistoryList` client island
 * — `localStorage` only exists in the browser, so there is nothing this
 * page itself can render server-side (frontend_plan.md §6, WS-E).
 */
export default function HistoryPage() {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">History</h1>
        <p className="text-muted-foreground">
          Questions and statements you&apos;ve run, stored locally in this browser (most recent 50).
        </p>
      </div>
      <HistoryList />
    </div>
  );
}
