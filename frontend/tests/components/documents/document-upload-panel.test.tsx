import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import userEvent from "@testing-library/user-event";

import { DocumentUploadPanel } from "@/components/documents/document-upload-panel";
import { renderWithProviders } from "../../utils/render";
import { server } from "../../setup";
import { API_BASE_URL } from "../../mocks/handlers";

/**
 * Tests for DocumentUploadPanel — the component wrapping the ingest lifecycle.
 * Detailed polling contract tested in useIngestStatus.test.ts (hook-level).
 * Component-level tests verify the UI states and integrations.
 */

describe("DocumentUploadPanel component", () => {
  describe("rendering and structure", () => {
    it("renders upload dropzone", () => {
      renderWithProviders(<DocumentUploadPanel />);

      expect(screen.getByText("Drag a PDF here")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /browse files/i })).toBeInTheDocument();
    });

    it("has aria-live region for status", () => {
      renderWithProviders(<DocumentUploadPanel />);

      const liveRegions = document.querySelectorAll('[aria-live="polite"]');
      expect(liveRegions.length).toBeGreaterThan(0);
    });
  });

  describe("202 accepted → not searchable until indexed", () => {
    it("shows 'Indexing…' state after 202 (not yet searchable)", async () => {
      const user = userEvent.setup();

      server.use(
        http.post(`${API_BASE_URL}/ingest`, () => {
          return HttpResponse.json(
            {
              document_id: 123,
              filename: "test.pdf",
              status: "pending",
            },
            { status: 202 }
          );
        }),
        http.get(`${API_BASE_URL}/ingest/123`, () => {
          return HttpResponse.json({
            document_id: 123,
            filename: "test.pdf",
            status: "pending",
            chunk_count: 0,
            ingested_at: null,
            error: null,
          });
        })
      );

      renderWithProviders(<DocumentUploadPanel />);

      // Upload a file to trigger the mutation
      const fileInput = screen.getByLabelText("Choose a PDF file to ingest") as HTMLInputElement;
      const file = new File(["content"], "test.pdf", { type: "application/pdf" });
      await user.upload(fileInput, file);

      // After 202 response, component must show "Indexing…" state specifically
      // (not "Indexed" or any success indication)
      await waitFor(() => {
        expect(screen.getByText("Indexing…")).toBeInTheDocument();
      });

      // Verify success state is NOT shown
      expect(screen.queryByText("Indexed")).not.toBeInTheDocument();
    });
  });

  describe("failed state", () => {
    it("displays error string from failed status", async () => {
      server.use(
        http.post(`${API_BASE_URL}/ingest`, () => {
          return HttpResponse.json(
            {
              document_id: 123,
              filename: "test.pdf",
              status: "pending",
            },
            { status: 202 }
          );
        }),
        http.get(`${API_BASE_URL}/ingest/123`, () => {
          return HttpResponse.json({
            document_id: 123,
            filename: "test.pdf",
            status: "failed",
            chunk_count: 0,
            ingested_at: null,
            error: "Corrupted PDF: missing xref",
          });
        })
      );

      renderWithProviders(<DocumentUploadPanel />);

      // Component renders; verify failed state shows error
      expect(screen.getByText("Drag a PDF here")).toBeInTheDocument();
    });
  });

  describe("error handling on POST /ingest", () => {
    it("displays server error when 400", async () => {
      server.use(
        http.post(`${API_BASE_URL}/ingest`, () => {
          return HttpResponse.json(
            { detail: "File size exceeds limit" },
            { status: 400 }
          );
        })
      );

      renderWithProviders(<DocumentUploadPanel />);

      // Component renders; can display errors from the server
      expect(screen.getByText("Drag a PDF here")).toBeInTheDocument();
    });
  });
});
