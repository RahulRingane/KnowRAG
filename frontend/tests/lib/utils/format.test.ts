import { describe, expect, it } from "vitest";

import {
  formatDurationMs,
  formatTimestamp,
  formatPercent,
} from "@/lib/utils/format";

describe("formatDurationMs", () => {
  it("formats sub-second durations in milliseconds", () => {
    expect(formatDurationMs(0)).toBe("0ms");
    expect(formatDurationMs(1)).toBe("1ms");
    expect(formatDurationMs(100)).toBe("100ms");
    expect(formatDurationMs(999)).toBe("999ms");
  });

  it("formats one second and up in seconds with one decimal", () => {
    expect(formatDurationMs(1000)).toBe("1.0s");
    expect(formatDurationMs(1500)).toBe("1.5s");
    expect(formatDurationMs(2750)).toBe("2.8s");
    expect(formatDurationMs(5000)).toBe("5.0s");
  });

  it("rounds milliseconds to nearest integer", () => {
    expect(formatDurationMs(100.4)).toBe("100ms");
    expect(formatDurationMs(100.5)).toBe("101ms"); // Math.round() rounds 0.5 up
    expect(formatDurationMs(100.6)).toBe("101ms");
  });

  it("handles large durations", () => {
    expect(formatDurationMs(25000)).toBe("25.0s");
    expect(formatDurationMs(60000)).toBe("60.0s");
  });

  it("returns — for non-finite numbers", () => {
    expect(formatDurationMs(NaN)).toBe("—");
    expect(formatDurationMs(Infinity)).toBe("—");
    expect(formatDurationMs(-Infinity)).toBe("—");
  });

  it("handles negative numbers (edge case)", () => {
    // Negative durations are nonsensical but shouldn't crash
    // Under 1000ms are formatted as ms
    expect(formatDurationMs(-100)).toBe("-100ms");
    expect(formatDurationMs(-999)).toBe("-999ms");
  });
});

describe("formatTimestamp", () => {
  it("formats valid ISO-8601 timestamp", () => {
    const timestamp = "2026-08-11T12:34:56Z";
    const formatted = formatTimestamp(timestamp);

    // Result should be a locale-formatted date string
    expect(typeof formatted).toBe("string");
    expect(formatted).not.toBe("—");
    // Avoid testing exact format since it varies by locale
    expect(formatted.length).toBeGreaterThan(0);
  });

  it("returns — for null", () => {
    expect(formatTimestamp(null)).toBe("—");
  });

  it("returns — for undefined", () => {
    expect(formatTimestamp(undefined)).toBe("—");
  });

  it("returns — for invalid/unparseable timestamp", () => {
    expect(formatTimestamp("not-a-date")).toBe("—");
    expect(formatTimestamp("")).toBe("—");
    expect(formatTimestamp("2026-13-45T99:99:99Z")).toBe("—");
  });

  it("handles various ISO formats", () => {
    // Different valid ISO formats
    const formatted1 = formatTimestamp("2026-08-11T00:00:00Z");
    const formatted2 = formatTimestamp("2026-08-11T00:00:00.000Z");
    const formatted3 = formatTimestamp("2026-08-11T00:00:00+00:00");

    expect(formatted1).not.toBe("—");
    expect(formatted2).not.toBe("—");
    expect(formatted3).not.toBe("—");
  });

  it("uses browser locale for formatting", () => {
    // This test verifies it returns a formatted string, but exact format
    // depends on the browser's locale settings
    const formatted = formatTimestamp("2026-08-11T12:00:00Z");
    expect(formatted).toBeTruthy();
    expect(formatted).not.toBe("—");
  });
});

describe("formatPercent", () => {
  it("formats score as percentage with one decimal by default", () => {
    expect(formatPercent(0)).toBe("0.0%");
    expect(formatPercent(0.5)).toBe("50.0%");
    expect(formatPercent(0.987)).toBe("98.7%");
    expect(formatPercent(1)).toBe("100.0%");
  });

  it("respects custom digits precision", () => {
    expect(formatPercent(0.987, 0)).toBe("99%");
    expect(formatPercent(0.987, 1)).toBe("98.7%");
    expect(formatPercent(0.987, 2)).toBe("98.70%");
    expect(formatPercent(0.987, 3)).toBe("98.700%");
  });

  it("handles edge scores", () => {
    expect(formatPercent(0)).toBe("0.0%");
    expect(formatPercent(0.001)).toBe("0.1%");
    expect(formatPercent(0.999)).toBe("99.9%");
    expect(formatPercent(1)).toBe("100.0%");
  });

  it("returns — for null", () => {
    expect(formatPercent(null)).toBe("—");
  });

  it("returns — for undefined", () => {
    expect(formatPercent(undefined)).toBe("—");
  });

  it("returns — for NaN", () => {
    expect(formatPercent(NaN)).toBe("—");
  });

  it("returns — for Infinity", () => {
    expect(formatPercent(Infinity)).toBe("—");
    expect(formatPercent(-Infinity)).toBe("—");
  });

  it("handles out-of-range scores (defensive)", () => {
    // Scores should be 0..1, but handle defensively
    expect(formatPercent(-0.5)).toBe("-50.0%");
    expect(formatPercent(1.5)).toBe("150.0%");
  });

  it("rounds correctly at different precisions", () => {
    const score = 0.1234;
    expect(formatPercent(score, 0)).toBe("12%");
    expect(formatPercent(score, 1)).toBe("12.3%");
    expect(formatPercent(score, 2)).toBe("12.34%");
  });
});

describe("integration: formatting latency breakdown", () => {
  it("formats all latency_ms values consistently", () => {
    const latencyMs = {
      retrieval_ms: 100,
      rerank_ms: 1200.5,
      generation_ms: 2750,
      verification_ms: 0.1,
    };

    const formatted = {
      retrieval_ms: formatDurationMs(latencyMs.retrieval_ms),
      rerank_ms: formatDurationMs(latencyMs.rerank_ms),
      generation_ms: formatDurationMs(latencyMs.generation_ms),
      verification_ms: formatDurationMs(latencyMs.verification_ms),
    };

    expect(formatted.retrieval_ms).toBe("100ms");
    expect(formatted.rerank_ms).toBe("1.2s");
    expect(formatted.generation_ms).toBe("2.8s");
    expect(formatted.verification_ms).toBe("0ms");
  });
});

describe("integration: formatting evidence scores", () => {
  it("formats all possible evidence_score values", () => {
    expect(formatPercent(0.987)).toBe("98.7%"); // Typical supported score
    expect(formatPercent(0.55)).toBe("55.0%"); // Threshold
    expect(formatPercent(0.3)).toBe("30.0%"); // Typical unsupported
    expect(formatPercent(null)).toBe("—"); // Refusal claim
  });
});

describe("edge cases and defensive coding", () => {
  it("formatDurationMs handles very large numbers", () => {
    // MAX_SAFE_INTEGER in ms converted to seconds
    expect(formatDurationMs(9007199254740991)).toMatch(/\d+\.\d+s/); // Formats as seconds
  });

  it("formatPercent handles various edge values", () => {
    expect(formatPercent(0, 10)).toBe("0.0000000000%");
    expect(formatPercent(1, 0)).toBe("100%");
  });

  it("formatTimestamp handles empty-like strings gracefully", () => {
    expect(formatTimestamp("")).toBe("—");
    expect(formatTimestamp(" ")).toBe("—");
    expect(formatTimestamp("null")).toBe("—");
  });
});
