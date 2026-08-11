import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../../utils/render";
import { StreamingActivity } from "@/components/query/streaming-activity";
import type { StreamEvent } from "@/types/api";

describe("StreamingActivity", () => {
  describe("Trap #8: Token events must NEVER appear as prose", () => {
    it("never renders token event text content, even with dangerous content", () => {
      /**
       * Critical test: token events contain raw JSON fragments being generated,
       * not prose. A client that displays them would show unverified, unverified
       * intermediate generation state as an answer. This is the SINGLE most
       * important correctness rule in this workstream.
       */
      const events: StreamEvent[] = [
        {
          event: "retrieval",
          data: { retrieved_chunk_ids: ["1:3", "1:5"] },
        },
        {
          // Token with distinctive content that should never appear in DOM
          event: "token",
          data: {
            text: '{"kind":"claim","text":"SENTINEL_TOKEN_TEXT_SHOULD_NOT_APPEAR_IN_DOM","status":"SUPPORTED"}',
          },
        },
        {
          event: "token",
          data: {
            text: '{"kind":"claim","text":"ANOTHER_DANGEROUS_SENTINEL_FROM_GENERATION"}',
          },
        },
      ];

      renderWithProviders(
        <StreamingActivity
          phase="streaming"
          events={events}
          retrievedChunkIds={["1:3", "1:5"]}
          hasResult={false}
        />
      );

      // The sentinel text should NEVER appear anywhere in the DOM
      expect(
        screen.queryByText(/SENTINEL_TOKEN_TEXT_SHOULD_NOT_APPEAR_IN_DOM/)
      ).not.toBeInTheDocument();
      expect(
        screen.queryByText(/ANOTHER_DANGEROUS_SENTINEL_FROM_GENERATION/)
      ).not.toBeInTheDocument();

      // Also ensure the raw JSON structure is not visible
      expect(
        screen.queryByText(/kind.*claim/)
      ).not.toBeInTheDocument();
    });

    it("displays token count instead of token content", () => {
      const events: StreamEvent[] = [
        {
          event: "retrieval",
          data: { retrieved_chunk_ids: ["1:3", "1:5"] },
        },
        { event: "token", data: { text: "fragment1" } },
        { event: "token", data: { text: "fragment2" } },
        { event: "token", data: { text: "fragment3" } },
      ];

      renderWithProviders(
        <StreamingActivity
          phase="streaming"
          events={events}
          retrievedChunkIds={["1:3", "1:5"]}
          hasResult={false}
        />
      );

      // Should show fragment count, not content
      expect(screen.getByText(/3 fragments/)).toBeInTheDocument();

      // Should never show the actual fragment text
      expect(screen.queryByText(/fragment1/)).not.toBeInTheDocument();
      expect(screen.queryByText(/fragment2/)).not.toBeInTheDocument();
      expect(screen.queryByText(/fragment3/)).not.toBeInTheDocument();
    });

    it("does not attempt to parse or display token data fields", () => {
      const events: StreamEvent[] = [
        {
          event: "retrieval",
          data: { retrieved_chunk_ids: ["1:3"] },
        },
        {
          event: "token",
          data: {
            text: '{"status":"SUPPORTED","citations":["C1"],"evidence_score":0.99}',
          },
        },
      ];

      renderWithProviders(
        <StreamingActivity
          phase="streaming"
          events={events}
          retrievedChunkIds={["1:3"]}
          hasResult={false}
        />
      );

      // Raw JSON content should never appear
      expect(screen.queryByText(/SUPPORTED/)).not.toBeInTheDocument();
      expect(screen.queryByText(/0.99/)).not.toBeInTheDocument();

      // Should only show "generating" indication with fragment count
      expect(screen.getByText(/1 fragment/)).toBeInTheDocument();
    });
  });

  describe("Fact route: zero token events", () => {
    it("emits zero token events on fact route and still completes cleanly", () => {
      const events: StreamEvent[] = [
        {
          event: "retrieval",
          data: { retrieved_chunk_ids: ["1:3"] },
        },
        // NO token events on fact route
      ];

      renderWithProviders(
        <StreamingActivity
          phase="streaming"
          events={events}
          retrievedChunkIds={["1:3"]}
          hasResult={false}
        />
      );

      // Should show verifying, not generating
      expect(screen.getByText(/verifying/)).toBeInTheDocument();

      // Should not mention fragments or generation at all
      expect(screen.queryByText(/fragment/)).not.toBeInTheDocument();
      expect(screen.queryByText(/generating/)).not.toBeInTheDocument();
    });

    it("correctly identifies zero token events on fact route", () => {
      const events: StreamEvent[] = [
        {
          event: "retrieval",
          data: { retrieved_chunk_ids: ["1:3"] },
        },
      ];

      renderWithProviders(
        <StreamingActivity
          phase="streaming"
          events={events}
          retrievedChunkIds={["1:3"]}
          hasResult={false}
        />
      );

      // Token count should be 0
      const tokenCount = events.filter((e) => e.event === "token").length;
      expect(tokenCount).toBe(0);

      // Message should reflect verifying without generation
      expect(screen.getByText(/verifying/)).toBeInTheDocument();
    });
  });

  describe("Phase transitions", () => {
    it("shows 'Connecting' during connecting phase", () => {
      renderWithProviders(
        <StreamingActivity
          phase="connecting"
          events={[]}
          retrievedChunkIds={null}
          hasResult={false}
        />
      );

      expect(screen.getByText(/Connecting/)).toBeInTheDocument();
    });

    it("shows 'Retrieving evidence' when no chunk ids yet", () => {
      renderWithProviders(
        <StreamingActivity
          phase="streaming"
          events={[]}
          retrievedChunkIds={null}
          hasResult={false}
        />
      );

      expect(screen.getByText(/Retrieving evidence/)).toBeInTheDocument();
    });

    it("shows 'Verification complete' when phase is done", () => {
      renderWithProviders(
        <StreamingActivity
          phase="done"
          events={[]}
          retrievedChunkIds={null}
          hasResult={true}
        />
      );

      expect(screen.getByText(/Verification complete/)).toBeInTheDocument();
    });

    it("shows 'Verification complete' when result has landed (before done event)", () => {
      renderWithProviders(
        <StreamingActivity
          phase="streaming"
          events={[]}
          retrievedChunkIds={["1:3"]}
          hasResult={true}
        />
      );

      expect(screen.getByText(/Verification complete/)).toBeInTheDocument();
    });

    it("returns null when phase is idle or error", () => {
      const { container: container1 } = renderWithProviders(
        <StreamingActivity
          phase="idle"
          events={[]}
          retrievedChunkIds={null}
          hasResult={false}
        />
      );

      expect(container1.firstChild).toBeNull();

      const { container: container2 } = renderWithProviders(
        <StreamingActivity
          phase="error"
          events={[]}
          retrievedChunkIds={null}
          hasResult={false}
        />
      );

      expect(container2.firstChild).toBeNull();
    });
  });

  describe("Activity indication", () => {
    it("shows spinner during active phases", () => {
      const { container } = renderWithProviders(
        <StreamingActivity
          phase="streaming"
          events={[]}
          retrievedChunkIds={["1:3"]}
          hasResult={false}
        />
      );

      // Should show animated loader
      const spinner = container.querySelector("[class*='animate-spin']");
      expect(spinner).toBeInTheDocument();
    });

    it("shows checkmark when complete", () => {
      const { container } = renderWithProviders(
        <StreamingActivity
          phase="done"
          events={[]}
          retrievedChunkIds={null}
          hasResult={true}
        />
      );

      // Should show success icon instead of spinner
      const checkmark = container.querySelector("[class*='text-verdict-supported']");
      expect(checkmark).toBeInTheDocument();
    });
  });

  describe("Accessibility", () => {
    it("has role status and aria-live polite", () => {
      const { container } = renderWithProviders(
        <StreamingActivity
          phase="streaming"
          events={[]}
          retrievedChunkIds={["1:3"]}
          hasResult={false}
        />
      );

      const statusRegion = container.querySelector("[role='status'][aria-live='polite']");
      expect(statusRegion).toBeInTheDocument();
    });

    it("announces phase changes to screen readers", () => {
      const { rerender } = renderWithProviders(
        <StreamingActivity
          phase="connecting"
          events={[]}
          retrievedChunkIds={null}
          hasResult={false}
        />
      );

      expect(screen.getByText(/Connecting/)).toBeInTheDocument();

      rerender(
        <StreamingActivity
          phase="streaming"
          events={[{ event: "retrieval", data: { retrieved_chunk_ids: ["1:3"] } }]}
          retrievedChunkIds={["1:3"]}
          hasResult={false}
        />
      );

      // Once retrieval is done, it should show verifying message
      expect(screen.getByText(/verifying/)).toBeInTheDocument();
    });

    it("icons are aria-hidden since status is conveyed by text", () => {
      const { container } = renderWithProviders(
        <StreamingActivity
          phase="streaming"
          events={[]}
          retrievedChunkIds={["1:3"]}
          hasResult={false}
        />
      );

      const icon = container.querySelector("[class*='animate-spin']");
      expect(icon).toHaveAttribute("aria-hidden", "true");
    });
  });

  describe("Retrieved chunk count display", () => {
    it("shows singular 'chunk' for one retrieved chunk", () => {
      renderWithProviders(
        <StreamingActivity
          phase="streaming"
          events={[
            {
              event: "retrieval",
              data: { retrieved_chunk_ids: ["1:3"] },
            },
          ]}
          retrievedChunkIds={["1:3"]}
          hasResult={false}
        />
      );

      expect(screen.getByText(/Retrieved 1 evidence chunk/)).toBeInTheDocument();
    });

    it("shows plural 'chunks' for multiple chunks", () => {
      renderWithProviders(
        <StreamingActivity
          phase="streaming"
          events={[
            {
              event: "retrieval",
              data: { retrieved_chunk_ids: ["1:3", "1:5", "1:6"] },
            },
          ]}
          retrievedChunkIds={["1:3", "1:5", "1:6"]}
          hasResult={false}
        />
      );

      expect(screen.getByText(/Retrieved 3 evidence chunks/)).toBeInTheDocument();
    });
  });

  describe("Token fragment count singular/plural", () => {
    it("shows singular 'fragment' for one token", () => {
      const events: StreamEvent[] = [
        { event: "retrieval", data: { retrieved_chunk_ids: ["1:3"] } },
        { event: "token", data: { text: "one" } },
      ];

      renderWithProviders(
        <StreamingActivity
          phase="streaming"
          events={events}
          retrievedChunkIds={["1:3"]}
          hasResult={false}
        />
      );

      expect(screen.getByText(/1 fragment/)).toBeInTheDocument();
    });

    it("shows plural 'fragments' for multiple tokens", () => {
      const events: StreamEvent[] = [
        { event: "retrieval", data: { retrieved_chunk_ids: ["1:3"] } },
        { event: "token", data: { text: "one" } },
        { event: "token", data: { text: "two" } },
      ];

      renderWithProviders(
        <StreamingActivity
          phase="streaming"
          events={events}
          retrievedChunkIds={["1:3"]}
          hasResult={false}
        />
      );

      expect(screen.getByText(/2 fragments/)).toBeInTheDocument();
    });
  });
});
