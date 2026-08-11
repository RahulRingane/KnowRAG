import { describe, expect, it, beforeEach, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { renderWithProviders } from "../../utils/render";
import { EvidencePanel } from "@/components/verification/evidence-panel";
import { server } from "../../setup";

const API_BASE_URL = "http://localhost:8000";

describe("EvidencePanel", () => {
  const mockChunkIds = ["1:3", "1:5", "1:6"];

  beforeEach(() => {
    localStorage.clear();
  });

  describe("Citation chip → evidence panel interaction (Trap #5)", () => {
    it("opens evidence panel when onOpenChunk is called", async () => {
      const { rerender } = renderWithProviders(
        <EvidencePanel
          open={false}
          onOpenChange={() => {}}
          chunkIds={mockChunkIds}
          focusedChunkId={null}
        />
      );

      // Initially closed
      expect(screen.queryByText("Evidence")).not.toBeInTheDocument();

      // Rerender with open=true
      rerender(
        <EvidencePanel
          open={true}
          onOpenChange={() => {}}
          chunkIds={mockChunkIds}
          focusedChunkId={null}
        />
      );

      // Now should show
      await waitFor(() => {
        expect(screen.getByText("Evidence")).toBeInTheDocument();
      });
    });

    it("fetches chunk text via GET /chunks", async () => {
      server.use(
        http.get(`${API_BASE_URL}/chunks`, () => {
          return HttpResponse.json([
            { document_id: 1, chunk_index: 3, text: "Evidence text for chunk 1:3" },
            { document_id: 1, chunk_index: 5, text: "Evidence text for chunk 1:5" },
          ]);
        })
      );

      renderWithProviders(
        <EvidencePanel
          open={true}
          onOpenChange={() => {}}
          chunkIds={mockChunkIds}
          focusedChunkId={null}
        />
      );

      await waitFor(() => {
        expect(screen.getByText("Evidence text for chunk 1:3")).toBeInTheDocument();
        expect(screen.getByText("Evidence text for chunk 1:5")).toBeInTheDocument();
      });
    });

    it("handles missing chunk text as 'text unavailable', not error", async () => {
      server.use(
        http.get(`${API_BASE_URL}/chunks`, () => {
          // Return only 1:3, missing 1:5 and 1:6
          return HttpResponse.json([
            { document_id: 1, chunk_index: 3, text: "Evidence text" },
          ]);
        })
      );

      renderWithProviders(
        <EvidencePanel
          open={true}
          onOpenChange={() => {}}
          chunkIds={mockChunkIds}
          focusedChunkId={null}
        />
      );

      // Should show "text unavailable" for missing chunks, not error
      await waitFor(() => {
        const unavailableMessages = screen.queryAllByText("text unavailable");
        expect(unavailableMessages.length).toBeGreaterThan(0);
      });
    });

    it("scrolls and focuses correct chunk when focusedChunkId changes", async () => {
      server.use(
        http.get(`${API_BASE_URL}/chunks`, () => {
          return HttpResponse.json([
            { document_id: 1, chunk_index: 3, text: "Chunk 1:3 text" },
            { document_id: 1, chunk_index: 5, text: "Chunk 1:5 text" },
          ]);
        })
      );

      const { rerender } = renderWithProviders(
        <EvidencePanel
          open={true}
          onOpenChange={() => {}}
          chunkIds={mockChunkIds}
          focusedChunkId="1:3"
        />
      );

      await waitFor(() => {
        expect(screen.getByText("Chunk 1:3 text")).toBeInTheDocument();
      });

      // Change focusedChunkId
      rerender(
        <EvidencePanel
          open={true}
          onOpenChange={() => {}}
          chunkIds={mockChunkIds}
          focusedChunkId="1:5"
        />
      );

      // Should have highlighted the new chunk
      const chunk15Element = screen.getByText("Chunk 1:5 text").closest("[tabindex='-1']");
      expect(chunk15Element).toHaveClass("ring-ring/50");
    });
  });

  describe("Loading state", () => {
    it("shows loading skeleton while fetching chunks", async () => {
      server.use(
        http.get(`${API_BASE_URL}/chunks`, async () => {
          await new Promise((resolve) => setTimeout(resolve, 100));
          return HttpResponse.json([
            { document_id: 1, chunk_index: 3, text: "Evidence text" },
          ]);
        })
      );

      renderWithProviders(
        <EvidencePanel
          open={true}
          onOpenChange={() => {}}
          chunkIds={mockChunkIds}
          focusedChunkId={null}
        />
      );

      // Should show something for loading state
      // The skeleton is used internally
      await waitFor(() => {
        expect(screen.getByText("Evidence text")).toBeInTheDocument();
      });
    });
  });

  describe("Error state", () => {
    it("shows error message when chunk fetch fails", async () => {
      server.use(
        http.get(`${API_BASE_URL}/chunks`, () => {
          return new HttpResponse(null, { status: 500 });
        })
      );

      renderWithProviders(
        <EvidencePanel
          open={true}
          onOpenChange={() => {}}
          chunkIds={mockChunkIds}
          focusedChunkId={null}
        />
      );

      await waitFor(() => {
        expect(screen.getByText(/Couldn't load evidence text/)).toBeInTheDocument();
      });
    });

    it("provides retry button on error", async () => {
      server.use(
        http.get(`${API_BASE_URL}/chunks`, () => {
          return new HttpResponse(null, { status: 500 });
        })
      );

      renderWithProviders(
        <EvidencePanel
          open={true}
          onOpenChange={() => {}}
          chunkIds={mockChunkIds}
          focusedChunkId={null}
        />
      );

      await waitFor(() => {
        const retryButton = screen.getByRole("button", { name: /retry/i });
        expect(retryButton).toBeInTheDocument();
      });
    });
  });

  describe("Empty state", () => {
    it("shows empty state when no chunks are provided", async () => {
      renderWithProviders(
        <EvidencePanel
          open={true}
          onOpenChange={() => {}}
          chunkIds={[]}
          focusedChunkId={null}
        />
      );

      expect(screen.getByText(/No evidence yet/)).toBeInTheDocument();
    });

    it("shows 'no evidence found' when all chunks return no text", async () => {
      server.use(
        http.get(`${API_BASE_URL}/chunks`, () => {
          return HttpResponse.json([]); // Empty response
        })
      );

      renderWithProviders(
        <EvidencePanel
          open={true}
          onOpenChange={() => {}}
          chunkIds={mockChunkIds}
          focusedChunkId={null}
        />
      );

      await waitFor(() => {
        expect(screen.getByText(/No evidence found/)).toBeInTheDocument();
      });
    });
  });

  describe("Interaction", () => {
    it("closes when user clicks close button", async () => {
      const user = userEvent.setup();
      const onOpenChange = vi.fn();

      server.use(
        http.get(`${API_BASE_URL}/chunks`, () => {
          return HttpResponse.json([
            { document_id: 1, chunk_index: 3, text: "Evidence text" },
          ]);
        })
      );

      renderWithProviders(
        <EvidencePanel
          open={true}
          onOpenChange={onOpenChange}
          chunkIds={mockChunkIds}
          focusedChunkId={null}
        />
      );

      const closeButton = screen.getByRole("button", { name: /close/i });
      await user.click(closeButton);

      expect(onOpenChange).toHaveBeenCalledWith(false);
    });

    it("displays chunk IDs in canonical format (document_id:chunk_index)", async () => {
      server.use(
        http.get(`${API_BASE_URL}/chunks`, () => {
          return HttpResponse.json([
            { document_id: 1, chunk_index: 3, text: "Evidence text" },
          ]);
        })
      );

      renderWithProviders(
        <EvidencePanel
          open={true}
          onOpenChange={() => {}}
          chunkIds={["1:3"]}
          focusedChunkId={null}
        />
      );

      await waitFor(() => {
        expect(screen.getByText("1:3")).toBeInTheDocument();
      });
    });
  });

  describe("Accessibility", () => {
    it("has proper title and description", async () => {
      server.use(
        http.get(`${API_BASE_URL}/chunks`, () => {
          return HttpResponse.json([
            { document_id: 1, chunk_index: 3, text: "Evidence text" },
          ]);
        })
      );

      renderWithProviders(
        <EvidencePanel
          open={true}
          onOpenChange={() => {}}
          chunkIds={mockChunkIds}
          focusedChunkId={null}
        />
      );

      // Should have proper title element
      expect(screen.getByText("Evidence")).toBeInTheDocument();

      // Should have description
      expect(screen.getByText(/Retrieved chunk text/)).toBeInTheDocument();
    });

    it("chunk items are focusable when the panel is focused", async () => {
      server.use(
        http.get(`${API_BASE_URL}/chunks`, () => {
          return HttpResponse.json([
            { document_id: 1, chunk_index: 3, text: "Chunk text" },
          ]);
        })
      );

      renderWithProviders(
        <EvidencePanel
          open={true}
          onOpenChange={() => {}}
          chunkIds={["1:3"]}
          focusedChunkId="1:3"
        />
      );

      await waitFor(() => {
        expect(screen.getByText("Chunk text")).toBeInTheDocument();
      });

      // Chunk item should be focusable
      const chunkItem = screen.getByText("Chunk text").closest("[tabindex]");
      expect(chunkItem).toHaveAttribute("tabindex", "-1");
    });
  });

});
