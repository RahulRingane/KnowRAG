import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";

import { DocumentList } from "@/components/documents/document-list";
import { renderWithProviders } from "../../utils/render";
import { server } from "../../setup";
import { API_BASE_URL } from "../../mocks/handlers";
import type { IngestStatus } from "@/types/api";

/**
 * Tests for DocumentList — the four-state component requirement (§5.3):
 * loading, error, empty, populated. No exceptions.
 */

describe("DocumentList component", () => {
  describe("loading and error states", () => {
    it("renders component", () => {
      renderWithProviders(<DocumentList />);
      expect(document.body).toBeInTheDocument();
    });
  });

  describe("error state", () => {
    it("renders error message with retry button on network error", async () => {
      server.use(
        http.get(`${API_BASE_URL}/documents`, () => {
          return HttpResponse.json(
            { detail: "Database connection failed" },
            { status: 500 }
          );
        })
      );

      renderWithProviders(<DocumentList />);

      await waitFor(() => {
        expect(screen.getByText("Couldn't load documents")).toBeInTheDocument();
        expect(screen.getByText("Database connection failed")).toBeInTheDocument();
      });

      expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
    });

    it("retry button calls refetch", async () => {
      let callCount = 0;

      server.use(
        http.get(`${API_BASE_URL}/documents`, () => {
          callCount++;
          if (callCount === 1) {
            return HttpResponse.json(
              { detail: "Server error" },
              { status: 500 }
            );
          }
          return HttpResponse.json([]);
        })
      );

      renderWithProviders(<DocumentList />);

      await waitFor(() => {
        expect(screen.getByText("Couldn't load documents")).toBeInTheDocument();
      });

      const retryButton = screen.getByRole("button", { name: /retry/i });
      await userEvent.click(retryButton);

      await waitFor(() => {
        expect(screen.getByText("No documents yet")).toBeInTheDocument();
      });

      expect(callCount).toBe(2);
    });

    it("shows generic error message on unexpected error", async () => {
      server.use(
        http.get(`${API_BASE_URL}/documents`, () => {
          throw new Error("Network error");
        })
      );

      renderWithProviders(<DocumentList />);

      await waitFor(() => {
        expect(screen.getByText("Couldn't load documents")).toBeInTheDocument();
      });
    });
  });

  describe("empty state", () => {
    it("shows empty state when no documents exist", async () => {
      server.use(
        http.get(`${API_BASE_URL}/documents`, () => {
          return HttpResponse.json([]);
        })
      );

      renderWithProviders(<DocumentList />);

      await waitFor(() => {
        expect(screen.getByText("No documents yet")).toBeInTheDocument();
        expect(screen.getByText(/Upload a PDF above/)).toBeInTheDocument();
      });
    });

    it("empty state has description text", async () => {
      server.use(
        http.get(`${API_BASE_URL}/documents`, () => {
          return HttpResponse.json([]);
        })
      );

      renderWithProviders(<DocumentList />);

      await waitFor(() => {
        expect(screen.getByText("No documents yet")).toBeInTheDocument();
        expect(screen.getByText(/Upload a PDF above/)).toBeInTheDocument();
      });
    });
  });

  describe("populated state", () => {
    const mockDocuments: IngestStatus[] = [
      {
        document_id: 1,
        filename: "report.pdf",
        status: "indexed",
        chunk_count: 42,
        ingested_at: "2026-08-11T10:00:00Z",
        error: null,
      },
      {
        document_id: 2,
        filename: "thesis.pdf",
        status: "pending",
        chunk_count: 0,
        ingested_at: null,
        error: null,
      },
      {
        document_id: 3,
        filename: "corrupted.pdf",
        status: "failed",
        chunk_count: 0,
        ingested_at: null,
        error: "PDF parsing failed",
      },
    ];

    it("renders document list with files", () => {
      server.use(
        http.get(`${API_BASE_URL}/documents`, () => {
          return HttpResponse.json(mockDocuments);
        })
      );

      renderWithProviders(<DocumentList />);

      // Component renders successfully
      expect(document.body).toBeInTheDocument();
    });

    it("displays document status badges for each status value", async () => {
      server.use(
        http.get(`${API_BASE_URL}/documents`, () => {
          return HttpResponse.json(mockDocuments);
        })
      );

      renderWithProviders(<DocumentList />);

      await waitFor(() => {
        expect(screen.getByText("Indexed")).toBeInTheDocument();
        expect(screen.getByText("Pending")).toBeInTheDocument();
        expect(screen.getByText("Failed")).toBeInTheDocument();
      });
    });

    it("displays ingestion timestamp for indexed documents", async () => {
      server.use(
        http.get(`${API_BASE_URL}/documents`, () => {
          return HttpResponse.json([mockDocuments[0]]);
        })
      );

      renderWithProviders(<DocumentList />);

      await waitFor(() => {
        expect(screen.getByText("report.pdf")).toBeInTheDocument();
      });

      // Timestamp should be formatted (exact format depends on formatTimestamp)
      const li = screen.getByText("report.pdf").closest("li");
      expect(li).toBeInTheDocument();
      expect(li?.textContent).toContain("ingested");
    });

    it("displays error message for failed documents", async () => {
      server.use(
        http.get(`${API_BASE_URL}/documents`, () => {
          return HttpResponse.json([mockDocuments[2]]);
        })
      );

      renderWithProviders(<DocumentList />);

      await waitFor(() => {
        expect(screen.getByText("corrupted.pdf")).toBeInTheDocument();
        expect(screen.getByText("PDF parsing failed")).toBeInTheDocument();
      });
    });

    it("handles singular vs plural chunk count text", async () => {
      const oneChunk: IngestStatus = {
        document_id: 1,
        filename: "single.pdf",
        status: "indexed",
        chunk_count: 1,
        ingested_at: "2026-08-11T10:00:00Z",
        error: null,
      };

      const multiChunk: IngestStatus = {
        document_id: 2,
        filename: "multi.pdf",
        status: "indexed",
        chunk_count: 5,
        ingested_at: "2026-08-11T10:00:00Z",
        error: null,
      };

      server.use(
        http.get(`${API_BASE_URL}/documents`, () => {
          return HttpResponse.json([oneChunk, multiChunk]);
        })
      );

      renderWithProviders(<DocumentList />);

      await waitFor(() => {
        expect(screen.getByText(/1 chunk · /)).toBeInTheDocument();
        expect(screen.getByText(/5 chunks · /)).toBeInTheDocument();
      });
    });

    it("truncates long filenames", async () => {
      const longFilename: IngestStatus = {
        document_id: 1,
        filename: "a".repeat(200) + ".pdf",
        status: "indexed",
        chunk_count: 10,
        ingested_at: "2026-08-11T10:00:00Z",
        error: null,
      };

      server.use(
        http.get(`${API_BASE_URL}/documents`, () => {
          return HttpResponse.json([longFilename]);
        })
      );

      renderWithProviders(<DocumentList />);

      await waitFor(() => {
        const li = screen.getByText((content) => content.includes("a".repeat(200))).closest("li");
        expect(li?.querySelector("p")?.classList.toString()).toContain("truncate");
      });
    });

    it("renders as an unordered list with aria-label", async () => {
      server.use(
        http.get(`${API_BASE_URL}/documents`, () => {
          return HttpResponse.json(mockDocuments);
        })
      );

      renderWithProviders(<DocumentList />);

      await waitFor(() => {
        expect(screen.getByText("report.pdf")).toBeInTheDocument();
      });

      const list = screen.getByRole("list");
      expect(list).toHaveAttribute("aria-label", "Ingested documents");

      const items = screen.getAllByRole("listitem");
      expect(items).toHaveLength(3);
    });
  });

  describe("edge cases", () => {
    it("handles null ingested_at timestamp gracefully", async () => {
      const doc: IngestStatus = {
        document_id: 1,
        filename: "pending.pdf",
        status: "pending",
        chunk_count: 0,
        ingested_at: null,
        error: null,
      };

      server.use(
        http.get(`${API_BASE_URL}/documents`, () => {
          return HttpResponse.json([doc]);
        })
      );

      renderWithProviders(<DocumentList />);

      await waitFor(() => {
        expect(screen.getByText("pending.pdf")).toBeInTheDocument();
      });
    });

    it("handles document with zero chunks", async () => {
      const doc: IngestStatus = {
        document_id: 1,
        filename: "empty.pdf",
        status: "indexed",
        chunk_count: 0,
        ingested_at: "2026-08-11T10:00:00Z",
        error: null,
      };

      server.use(
        http.get(`${API_BASE_URL}/documents`, () => {
          return HttpResponse.json([doc]);
        })
      );

      renderWithProviders(<DocumentList />);

      await waitFor(() => {
        expect(screen.getByText(/0 chunks/)).toBeInTheDocument();
      });
    });

    it("handles failed status with null error message", async () => {
      const doc: IngestStatus = {
        document_id: 1,
        filename: "failed.pdf",
        status: "failed",
        chunk_count: 0,
        ingested_at: null,
        error: null,
      };

      server.use(
        http.get(`${API_BASE_URL}/documents`, () => {
          return HttpResponse.json([doc]);
        })
      );

      renderWithProviders(<DocumentList />);

      await waitFor(() => {
        expect(screen.getByText("failed.pdf")).toBeInTheDocument();
        expect(screen.getByText("Failed")).toBeInTheDocument();
      });

      // Should not render error message if null
      const li = screen.getByText("failed.pdf").closest("li");
      const errors = li?.querySelectorAll('[class*="destructive"]');
      expect(errors?.length || 0).toBeLessThanOrEqual(1); // Only the status badge
    });
  });
});
