import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as nextNav from "next/navigation";

import { renderWithProviders } from "../../utils/render";
import { QueryWorkspace } from "@/components/query/query-workspace";
import { server } from "../../setup";
import { RERUN_QUERY_PARAM } from "@/components/history/history-list";
import { questionRouteOkResponse } from "../verification/fixtures";
import { readHistory } from "@/lib/storage/history";

const API_BASE_URL = "http://localhost:8000";

vi.mock("next/navigation");

// Read history through the module that owns the storage key rather than
// re-deriving it. An earlier version of this file hardcoded the key as
// "query_history"; the real one is "knowrag:history", so every history
// assertion read an empty slot and looked like a missing write.


/**
 * `useSearchParams` returns Next's `ReadonlyURLSearchParams`, which a plain
 * `URLSearchParams` satisfies at runtime but not structurally at compile
 * time. Funnelling the cast through one helper keeps it in a single place
 * instead of an `any` at each call site.
 */
type MockedSearchParams = ReturnType<typeof nextNav.useSearchParams>;
const asSearchParams = (params: URLSearchParams) => params as unknown as MockedSearchParams;

describe("QueryWorkspace — integration seams", () => {
  let mockRouter: ReturnType<typeof nextNav.useRouter>;
  let queryPostCount = 0;

  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
    queryPostCount = 0;

    mockRouter = {
      push: vi.fn(),
      replace: vi.fn(),
      back: vi.fn(),
      forward: vi.fn(),
      refresh: vi.fn(),
      prefetch: vi.fn(),
    } as unknown as ReturnType<typeof nextNav.useRouter>;

    vi.mocked(nextNav.useRouter).mockReturnValue(mockRouter);
    vi.mocked(nextNav.usePathname).mockReturnValue("/");
    vi.mocked(nextNav.useSearchParams).mockReturnValue(asSearchParams(new URLSearchParams()));

    // Track query POST calls
    server.use(
      http.post(`${API_BASE_URL}/query`, () => {
        queryPostCount++;
        return HttpResponse.json(questionRouteOkResponse);
      })
    );
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  describe("Seam 1: History recording on success (useRef guard prevents duplicates)", () => {
    it("records exactly one history entry on successful query (useRef guard), not ≥1", async () => {
      const user = userEvent.setup();

      renderWithProviders(<QueryWorkspace />);

      // `QueryWorkspace` defaults to `streaming = true`, so a bare submit goes
      // down the SSE path (`POST /query/stream`) — which the handler above does
      // NOT mock, leaving the request hanging forever. Toggling streaming off
      // is what routes this through `useQuerySubmit`/`POST /query`. An earlier
      // pass mistook that hang for "the mutation is untestable in jsdom"; it
      // isn't, it was simply exercising a different endpoint.
      await user.click(screen.getByRole("button", { name: /streaming/i }));

      const textarea = screen.getByLabelText(/Ask a question, or state something to fact-check/i);
      await user.type(textarea, "What is RISC?");
      await user.click(screen.getByRole("button", { name: /^submit$/i }));

      // `toHaveLength(1)` exactly, never `>= 1`: the whole point of the
      // `recordedStreamResultRef` guard is that re-renders after a result
      // lands must not append a second entry. A `>= 1` assertion would pass
      // even with the guard removed, i.e. it would be vacuous.
      await waitFor(() => {
        expect(readHistory()).toHaveLength(1);
      });

      expect(readHistory()[0]?.question).toBe(questionRouteOkResponse.question);
    });
  });

  describe("Seam 2: Prefill from re-run param, but NO auto-submit (guards billed LLM call)", () => {
    it("prefills textarea from param and does NOT auto-submit", async () => {
      const searchParams = new URLSearchParams();
      searchParams.set(RERUN_QUERY_PARAM, "What is pipelining?");
      vi.mocked(nextNav.useSearchParams).mockReturnValue(asSearchParams(searchParams));

      renderWithProviders(<QueryWorkspace />);

      const textarea = screen.getByLabelText(/Ask a question, or state something to fact-check/i) as HTMLTextAreaElement;

      // Prefilled with the re-run param value
      await waitFor(() => {
        expect(textarea.value).toBe("What is pipelining?");
      });

      // CRITICAL: No POST /query should have been made on mount
      // A billed LLM call firing from a link click would be a serious bug.
      expect(queryPostCount).toBe(0);
    });

    it("allows manual submit after prefill without double query", async () => {
      const user = userEvent.setup();
      const searchParams = new URLSearchParams();
      searchParams.set(RERUN_QUERY_PARAM, "What is RISC?");
      vi.mocked(nextNav.useSearchParams).mockReturnValue(asSearchParams(searchParams));

      renderWithProviders(<QueryWorkspace />);

      // Prefilled but not submitted yet
      await waitFor(() => {
        const textarea = screen.getByLabelText(/Ask a question, or state something to fact-check/i) as HTMLTextAreaElement;
        expect(textarea.value).toBe("What is RISC?");
      });

      // Zero queries on mount (no auto-submit)
      expect(queryPostCount).toBe(0);

      // Streaming is on by default; toggle it off so the manual submit goes
      // through `POST /query` (the mocked route) rather than `/query/stream`.
      await user.click(screen.getByRole("button", { name: /streaming/i }));

      await user.click(screen.getByRole("button", { name: /^submit$/i }));

      // Exactly one call: the user's own submit, and no second one from the
      // prefill effect re-firing.
      await waitFor(() => {
        expect(queryPostCount).toBe(1);
      });
    });
  });

  describe("Seam 3: Real cancellation via AbortController (no error state, no history write)", () => {
    it("cancels query without error state or history write", async () => {
      const user = userEvent.setup();

      // Override the handler to delay indefinitely (simulates slow query)
      server.use(
        http.post(`${API_BASE_URL}/query`, async () => {
          await new Promise((resolve) => setTimeout(resolve, 5000));
          return HttpResponse.json(questionRouteOkResponse);
        })
      );

      renderWithProviders(<QueryWorkspace />);

      const textarea = screen.getByLabelText(/Ask a question, or state something to fact-check/i);
      const submitButton = screen.getByRole("button", { name: /^submit$/i });

      // Turn off streaming to test non-streaming cancel (AbortController path)
      const streamingToggle = screen.getByRole("button", { name: /streaming/i });
      await user.click(streamingToggle);

      await user.type(textarea, "What is RISC?");
      await user.click(submitButton);

      // Query should be in flight
      await waitFor(() => {
        expect(screen.getByText(/Retrieving evidence and checking it/i)).toBeInTheDocument();
      });

      // Click cancel
      const cancelButton = screen.getByRole("button", { name: /cancel/i });
      await user.click(cancelButton);

      // Assert NO error state is rendered
      await waitFor(() => {
        expect(screen.queryByText(/The query failed|error/i)).not.toBeInTheDocument();
      });

      // Assert history was NOT written (cancel suppresses onSuccess)
      const history = readHistory();
      expect(history).toHaveLength(0);
    });
  });

  describe("Basic rendering and accessibility", () => {
    it("renders input with proper labels", () => {
      renderWithProviders(<QueryWorkspace />);

      expect(screen.getByLabelText(/Ask a question, or state something to fact-check/i)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /^submit$/i })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /streaming/i })).toBeInTheDocument();
    });

    it("has descriptive hint text", () => {
      renderWithProviders(<QueryWorkspace />);

      expect(screen.getByText(/A trailing.*routes to an answer/i)).toBeInTheDocument();
    });
  });
});
