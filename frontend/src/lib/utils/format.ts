/**
 * Presentation-only formatters for durations, timestamps, and scores.
 * Kept deliberately free of any API/domain types so these can be unit
 * tested (WS-F) with plain numbers/strings and reused anywhere.
 */

/**
 * Formats a millisecond duration for the latency breakdown
 * (`FactCheckedResponse.latency_ms`, an open-ended `Record<string,
 * number>` — see `types/api.ts`). Sub-second durations render as whole
 * milliseconds (`"340ms"`); one second and up render in seconds with one
 * decimal place (`"1.2s"`), matching the CLI's own `took`/`timing` output
 * style (CLAUDE.md "Latency and the timing breakdown").
 */
export function formatDurationMs(ms: number): string {
  if (!Number.isFinite(ms)) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

/**
 * Formats an ISO-8601 timestamp for display using the browser's locale.
 * `IngestStatus.ingested_at` is `string | null` (still pending), so `null`
 * and `undefined` both render as `"—"` rather than the caller needing an
 * `ingested_at ? ... : ...` guard at every call site. An unparseable
 * string (backend contract drift) also renders as `"—"` instead of
 * `"Invalid Date"` leaking into the UI.
 */
export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString();
}

/**
 * Formats a `ClaimVerdict.evidence_score` (a `0..1` NLI entailment
 * probability, or `null` for a `REFUSAL` claim that was never scored) as a
 * percentage. `digits` controls decimal precision — evidence scores are
 * often meaningfully different at the tenths of a percent (e.g. the
 * 0.55 SUPPORTED threshold, CLAUDE.md "Fact-check flow"), so the default
 * keeps one decimal place rather than rounding to whole percent.
 */
export function formatPercent(score: number | null | undefined, digits = 1): string {
  if (score === null || score === undefined || !Number.isFinite(score)) return "—";
  return `${(score * 100).toFixed(digits)}%`;
}
