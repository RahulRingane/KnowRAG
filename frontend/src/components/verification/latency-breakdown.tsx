import { formatDurationMs } from "@/lib/utils/format";

/**
 * `latency_ms` is an open-ended `Record<string, number>` whose keys differ
 * by route (trap #6) — the question route has `generation_ms`, the fact
 * route doesn't; `/query/stream` additionally omits `rerank_ms` and
 * `model_load_ms`. This component makes no assumption about which keys
 * exist: it just iterates `Object.entries(latency_ms)`.
 *
 * The keys are documented as DISJOINT spans (CLAUDE.md "Latency and the
 * timing breakdown": `_stage()` subtracts sub-costs back out of the stage
 * that contained them specifically so nothing double-counts), so
 * `sum(values)` is honest wall time and a stacked bar is a legitimate,
 * non-misleading representation — not just a convenient one.
 *
 * A handful of stages (typically ≤6) means a bespoke chart component would
 * be overkill; this is a flex div with computed widths, the one place §5.3
 * sanctions inline `style` (the width can't be expressed as a static
 * Tailwind class since it's a runtime percentage). Colour is NOT the only
 * way a segment's identity is conveyed — every segment has a real text
 * label with its name and duration in the legend below the bar, so a
 * colour-vision-limited reader loses nothing by not being able to
 * distinguish two adjacent grey steps in the bar itself.
 */
const SEGMENT_CLASSES = ["bg-chart-1", "bg-chart-2", "bg-chart-3", "bg-chart-4", "bg-chart-5"];

export function LatencyBreakdown({ latencyMs }: { latencyMs: Record<string, number> }) {
  const entries = Object.entries(latencyMs);
  if (entries.length === 0) return null;

  const total = entries.reduce((sum, [, value]) => sum + value, 0);

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-xs">
        <span className="font-medium">Latency breakdown</span>
        <span className="text-muted-foreground">total {formatDurationMs(total)}</span>
      </div>

      <div
        className="bg-muted flex h-2.5 w-full gap-[2px] overflow-hidden rounded-full"
        role="img"
        aria-label={`Latency breakdown: ${entries
          .map(([name, ms]) => `${name} ${formatDurationMs(ms)}`)
          .join(", ")}, total ${formatDurationMs(total)}`}
      >
        {entries.map(([name, ms], i) => {
          const pct = total > 0 ? (ms / total) * 100 : 0;
          const colorClass = SEGMENT_CLASSES[i % SEGMENT_CLASSES.length];
          return (
            <div
              key={name}
              className={`${colorClass} h-full first:rounded-l-full last:rounded-r-full`}
              style={{ width: `${pct}%` }}
            />
          );
        })}
      </div>

      <ul className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
        {entries.map(([name, ms], i) => {
          const colorClass = SEGMENT_CLASSES[i % SEGMENT_CLASSES.length];
          const pct = total > 0 ? (ms / total) * 100 : 0;
          return (
            <li key={name} className="flex items-center gap-1.5">
              <span className={`${colorClass} size-2 shrink-0 rounded-full`} aria-hidden="true" />
              <span className="text-muted-foreground truncate">{name}</span>
              <span className="ml-auto tabular-nums">{formatDurationMs(ms)}</span>
              <span className="text-muted-foreground w-9 shrink-0 text-right tabular-nums">
                {pct.toFixed(0)}%
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
