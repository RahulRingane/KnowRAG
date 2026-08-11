import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import React from "react";

import { useIngestStatus } from "@/lib/hooks/useIngestStatus";
import { server } from "../../setup";
import { API_BASE_URL } from "../../mocks/handlers";

/**
 * Direct tests of useIngestStatus hook polling contract.
 * CRITICAL: Polling must stop on terminal state (indexed/failed).
 * This is a real bug class — a poll that never stops hammers the backend forever.
 */

describe("useIngestStatus hook — polling termination", () => {
  beforeEach(() => {
    // shouldAdvanceTime: true keeps the event loop turning so MSW promises settle
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  const createQueryClientWrapper = () => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
      },
    });

    // Named rather than an anonymous arrow so eslint's react/display-name
    // rule is satisfied: this is a test wrapper, not a real component.
    return function QueryClientWrapper({ children }: { children: React.ReactNode }) {
      return React.createElement(QueryClientProvider, { client: queryClient }, children);
    };
  };

  describe("polling stops on indexed (terminal state)", () => {
    it("stops polling after status reaches 'indexed'", async () => {
      let pollCount = 0;

      server.use(
        http.get(`${API_BASE_URL}/ingest/123`, () => {
          pollCount++;
          if (pollCount === 1) {
            return HttpResponse.json({
              document_id: 123,
              filename: "test.pdf",
              status: "pending",
              chunk_count: 0,
              ingested_at: null,
              error: null,
            });
          }

          // Return indexed on all subsequent polls
          return HttpResponse.json({
            document_id: 123,
            filename: "test.pdf",
            status: "indexed",
            chunk_count: 42,
            ingested_at: "2026-08-11T10:00:00Z",
            error: null,
          });
        })
      );

      const { result } = renderHook(() => useIngestStatus(123), {
        wrapper: createQueryClientWrapper(),
      });

      // First poll: pending
      await waitFor(() => {
        expect(result.current.data?.status).toBe("pending");
      });

      expect(pollCount).toBe(1);

      // Advance past next poll interval (2s)
      await vi.advanceTimersByTimeAsync(2000);

      // Second poll: indexed
      await waitFor(() => {
        expect(result.current.data?.status).toBe("indexed");
      });

      expect(pollCount).toBe(2);

      // Capture count when terminal state reached
      const pollCountAtTerminal = pollCount;

      // Advance time multiple intervals — should NOT make more requests
      await vi.advanceTimersByTimeAsync(2000); // 3rd interval
      await vi.advanceTimersByTimeAsync(2000); // 4th interval
      await vi.advanceTimersByTimeAsync(2000); // 5th interval

      // Poll count should NOT have increased
      expect(pollCount).toBe(pollCountAtTerminal);
    });
  });

  describe("polling stops on failed (terminal state)", () => {
    it("stops polling after status reaches 'failed'", async () => {
      let pollCount = 0;

      server.use(
        http.get(`${API_BASE_URL}/ingest/456`, () => {
          pollCount++;
          if (pollCount <= 2) {
            return HttpResponse.json({
              document_id: 456,
              filename: "bad.pdf",
              status: "pending",
              chunk_count: 0,
              ingested_at: null,
              error: null,
            });
          }

          // Return failed on 3rd poll and beyond
          return HttpResponse.json({
            document_id: 456,
            filename: "bad.pdf",
            status: "failed",
            chunk_count: 0,
            ingested_at: null,
            error: "PDF parsing failed",
          });
        })
      );

      const { result } = renderHook(() => useIngestStatus(456), {
        wrapper: createQueryClientWrapper(),
      });

      // First poll: pending
      await waitFor(() => {
        expect(result.current.data?.status).toBe("pending");
      });

      expect(pollCount).toBe(1);

      // Advance to 2nd poll
      await vi.advanceTimersByTimeAsync(2000);

      await waitFor(() => {
        expect(result.current.data?.status).toBe("pending");
      });

      expect(pollCount).toBe(2);

      // Advance to 3rd poll: failed
      await vi.advanceTimersByTimeAsync(2000);

      await waitFor(() => {
        expect(result.current.data?.status).toBe("failed");
      });

      expect(pollCount).toBe(3);

      const pollCountAtTerminal = pollCount;

      // Advance multiple intervals — should NOT poll anymore
      await vi.advanceTimersByTimeAsync(2000);
      await vi.advanceTimersByTimeAsync(2000);
      await vi.advanceTimersByTimeAsync(2000);
      await vi.advanceTimersByTimeAsync(2000);

      // No additional polls should have been made
      expect(pollCount).toBe(pollCountAtTerminal);
    });
  });

  describe("202 trap: pending status is NOT searchable", () => {
    it("pending status stays pending until indexed", async () => {
      server.use(
        http.get(`${API_BASE_URL}/ingest/789`, () => {
          return HttpResponse.json({
            document_id: 789,
            filename: "pending.pdf",
            status: "pending",
            chunk_count: 0,
            ingested_at: null,
            error: null,
          });
        })
      );

      const { result } = renderHook(() => useIngestStatus(789), {
        wrapper: createQueryClientWrapper(),
      });

      await waitFor(() => {
        expect(result.current.data?.status).toBe("pending");
      });

      // Exact assertion: status must be "pending", not "indexed"
      expect(result.current.data?.status).toBe("pending");
      expect(result.current.data?.chunk_count).toBe(0);
      expect(result.current.data?.ingested_at).toBeNull();
    });
  });
});
