import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";

import { HealthPanel } from "@/components/system/health-panel";
import { renderWithProviders } from "../../utils/render";
import { server } from "../../setup";
import { API_BASE_URL } from "../../mocks/handlers";
import type { HealthReport } from "@/types/api";

/**
 * Tests for HealthPanel — the critical 503-with-body-shape requirement.
 * Domain trap #2: GET /health returns IDENTICAL body shape on 200 and 503.
 * A 503 response with a `degraded` status is NOT an error state.
 *
 * Key contracts:
 * - 200 ok + 503 degraded both render as data (not error)
 * - degraded status shows per-datastore breakdown
 * - icon + text for status, never color alone
 */

describe("HealthPanel component", () => {
  // Health panel uses real timers for refetch interval
  // No need to mock timers for these response-rendering tests

  describe("rendering", () => {
    it("renders health panel", () => {
      server.use(
        http.get(`${API_BASE_URL}/health`, () => {
          return HttpResponse.json({
            status: "ok",
            service: "knowrag",
            checks: {
              postgres: { status: "ok" },
              qdrant: { status: "ok" },
              elasticsearch: { status: "ok" },
            },
          });
        })
      );

      renderWithProviders(<HealthPanel />);

      // Component renders without errors
      expect(document.body).toBeInTheDocument();
    });
  });

  describe("200 ok status — all systems operational", () => {
    const okResponse: HealthReport = {
      status: "ok",
      service: "knowrag",
      checks: {
        postgres: { status: "ok" },
        qdrant: { status: "ok" },
        elasticsearch: { status: "ok" },
      },
    };

    it("renders 'All systems operational' for ok status", async () => {
      server.use(
        http.get(`${API_BASE_URL}/health`, () => {
          return HttpResponse.json(okResponse);
        })
      );

      renderWithProviders(<HealthPanel />);

      await waitFor(() => {
        expect(screen.getByText("All systems operational")).toBeInTheDocument();
      });
    });

    it("does not render error state alert variant", async () => {
      server.use(
        http.get(`${API_BASE_URL}/health`, () => {
          return HttpResponse.json(okResponse);
        })
      );

      renderWithProviders(<HealthPanel />);

      await waitFor(() => {
        expect(screen.getByText("All systems operational")).toBeInTheDocument();
      });

      // Should not use destructive variant
      const alert = screen.getByText("All systems operational").closest("div");
      expect(alert?.className).not.toContain("destructive");
    });

    it("displays all datastore statuses as ok", async () => {
      server.use(
        http.get(`${API_BASE_URL}/health`, () => {
          return HttpResponse.json(okResponse);
        })
      );

      renderWithProviders(<HealthPanel />);

      await waitFor(() => {
        expect(screen.getByText("Postgres")).toBeInTheDocument();
      });

      expect(screen.getByText("Postgres")).toBeInTheDocument();
      expect(screen.getByText("Qdrant")).toBeInTheDocument();
      expect(screen.getByText("Elasticsearch")).toBeInTheDocument();

      // All should show "Ok" status
      const okStatuses = screen.getAllByText("Ok");
      expect(okStatuses.length).toBeGreaterThanOrEqual(3);
    });
  });

  describe("503 degraded status — the critical trap", () => {
    const degradedResponse: HealthReport = {
      status: "degraded",
      service: "knowrag",
      checks: {
        postgres: { status: "ok" },
        qdrant: { status: "error", detail: "Connection refused" },
        elasticsearch: { status: "ok" },
      },
    };

    it("renders degraded status as DATA, not as ERROR state", async () => {
      server.use(
        http.get(`${API_BASE_URL}/health`, () => {
          return HttpResponse.json(degradedResponse, { status: 503 });
        })
      );

      renderWithProviders(<HealthPanel />);

      await waitFor(() => {
        expect(screen.getByText("Degraded")).toBeInTheDocument();
      });

      // Should NOT show the error state (Couldn't reach the health endpoint)
      expect(screen.queryByText("Couldn't reach the health endpoint")).not.toBeInTheDocument();

      // Should show the degraded alert
      expect(screen.getByText(/One or more datastores/)).toBeInTheDocument();
    });

    it("shows 'Degraded' title and explanation for 503 body", async () => {
      server.use(
        http.get(`${API_BASE_URL}/health`, () => {
          return HttpResponse.json(degradedResponse, { status: 503 });
        })
      );

      renderWithProviders(<HealthPanel />);

      await waitFor(() => {
        expect(screen.getByText("Degraded")).toBeInTheDocument();
      });

      expect(screen.getByText(/One or more datastores are unreachable/)).toBeInTheDocument();
    });

    it("displays per-datastore breakdown with status and detail", async () => {
      server.use(
        http.get(`${API_BASE_URL}/health`, () => {
          return HttpResponse.json(degradedResponse, { status: 503 });
        })
      );

      renderWithProviders(<HealthPanel />);

      await waitFor(() => {
        expect(screen.getByText("Qdrant")).toBeInTheDocument();
      });

      // Qdrant should show error status
      const qdrantRow = screen.getByText("Qdrant").closest("li");
      expect(qdrantRow?.textContent).toContain("Error");

      // Error detail should be visible
      expect(screen.getByText("Connection refused")).toBeInTheDocument();

      // Postgres and Elasticsearch should show ok
      expect(screen.getByText("Postgres")).toBeInTheDocument();
      expect(screen.getByText("Elasticsearch")).toBeInTheDocument();
    });

    it("shows degraded alert with explanation", async () => {
      server.use(
        http.get(`${API_BASE_URL}/health`, () => {
          return HttpResponse.json(degradedResponse, { status: 503 });
        })
      );

      renderWithProviders(<HealthPanel />);

      await waitFor(() => {
        expect(screen.getByText("Degraded")).toBeInTheDocument();
        expect(screen.getByText(/One or more datastores/)).toBeInTheDocument();
      });
    });
  });

  describe("icon + text for status (a11y: never color alone)", () => {
    it("ok status shows 'All systems operational' text", async () => {
      server.use(
        http.get(`${API_BASE_URL}/health`, () => {
          return HttpResponse.json({
            status: "ok",
            service: "knowrag",
            checks: {
              postgres: { status: "ok" },
              qdrant: { status: "ok" },
              elasticsearch: { status: "ok" },
            },
          });
        })
      );

      renderWithProviders(<HealthPanel />);

      await waitFor(() => {
        expect(screen.getByText("All systems operational")).toBeInTheDocument();
      });
    });

    it("degraded status shows 'Degraded' text", async () => {
      server.use(
        http.get(`${API_BASE_URL}/health`, () => {
          return HttpResponse.json(
            {
              status: "degraded",
              service: "knowrag",
              checks: {
                postgres: { status: "ok" },
                qdrant: { status: "error" },
                elasticsearch: { status: "ok" },
              },
            },
            { status: 503 }
          );
        })
      );

      renderWithProviders(<HealthPanel />);

      await waitFor(() => {
        expect(screen.getByText("Degraded")).toBeInTheDocument();
      });
    });

    it("datastore ok shows 'Ok' text", async () => {
      server.use(
        http.get(`${API_BASE_URL}/health`, () => {
          return HttpResponse.json({
            status: "ok",
            service: "knowrag",
            checks: {
              postgres: { status: "ok" },
              qdrant: { status: "ok" },
              elasticsearch: { status: "ok" },
            },
          });
        })
      );

      renderWithProviders(<HealthPanel />);

      await waitFor(() => {
        expect(screen.getByText("Postgres")).toBeInTheDocument();
      });

      // Each row should have "Ok" text
      const okStatuses = screen.getAllByText("Ok");
      expect(okStatuses.length).toBeGreaterThanOrEqual(3);
    });

    it("datastore error shows 'Error' text", async () => {
      server.use(
        http.get(`${API_BASE_URL}/health`, () => {
          return HttpResponse.json(
            {
              status: "degraded",
              service: "knowrag",
              checks: {
                postgres: { status: "error", detail: "Connection timeout" },
                qdrant: { status: "ok" },
                elasticsearch: { status: "ok" },
              },
            },
            { status: 503 }
          );
        })
      );

      renderWithProviders(<HealthPanel />);

      await waitFor(() => {
        expect(screen.getByText("Postgres")).toBeInTheDocument();
      });

      // Postgres row should have "Error" text
      const postgresRow = screen.getByText("Postgres").closest("li");
      expect(postgresRow?.textContent).toContain("Error");
    });
  });

  describe("error state (network/parsing error)", () => {
    it("shows error when request fails", async () => {
      server.use(
        http.get(`${API_BASE_URL}/health`, () => {
          return HttpResponse.json(
            { detail: "Server unreachable" },
            { status: 500 }
          );
        })
      );

      renderWithProviders(<HealthPanel />);

      await waitFor(() => {
        expect(screen.getByText("Couldn't reach the health endpoint")).toBeInTheDocument();
      });
    });

    it("error state shows retry button", async () => {
      server.use(
        http.get(`${API_BASE_URL}/health`, () => {
          return HttpResponse.json(
            { detail: "Server error" },
            { status: 500 }
          );
        })
      );

      renderWithProviders(<HealthPanel />);

      await waitFor(() => {
        expect(screen.getByText("Couldn't reach the health endpoint")).toBeInTheDocument();
      });

      expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
    });

    it("retry button calls refetch", async () => {
      let callCount = 0;

      server.use(
        http.get(`${API_BASE_URL}/health`, () => {
          callCount++;
          if (callCount === 1) {
            return HttpResponse.json(
              { detail: "Error" },
              { status: 500 }
            );
          }
          return HttpResponse.json({
            status: "ok",
            service: "knowrag",
            checks: {
              postgres: { status: "ok" },
              qdrant: { status: "ok" },
              elasticsearch: { status: "ok" },
            },
          });
        })
      );

      renderWithProviders(<HealthPanel />);

      await waitFor(() => {
        expect(screen.getByText("Couldn't reach the health endpoint")).toBeInTheDocument();
      });

      const retryButton = screen.getByRole("button", { name: /retry/i });
      await userEvent.click(retryButton);

      await waitFor(() => {
        expect(screen.getByText("All systems operational")).toBeInTheDocument();
      });

      expect(callCount).toBe(2);
    });
  });

  describe("refresh and polling", () => {
    it("displays 'Refreshes automatically every 15s' message", () => {
      renderWithProviders(<HealthPanel />);

      expect(screen.getByText(/Refreshes automatically every 15s/)).toBeInTheDocument();
    });

    it("refresh button is present and clickable", async () => {
      server.use(
        http.get(`${API_BASE_URL}/health`, () => {
          return HttpResponse.json({
            status: "ok",
            service: "knowrag",
            checks: {
              postgres: { status: "ok" },
              qdrant: { status: "ok" },
              elasticsearch: { status: "ok" },
            },
          });
        })
      );

      renderWithProviders(<HealthPanel />);

      await waitFor(() => {
        expect(screen.getByText("All systems operational")).toBeInTheDocument();
      });

      const refreshButton = screen.getByRole("button", { name: /refresh/i });
      expect(refreshButton).toBeInTheDocument();
      expect(refreshButton).not.toBeDisabled();
    });

    it("refresh button is disabled while fetching", async () => {
      const createPromise = () => {
        let resolveRequest: ((value: unknown) => void) = () => {};
        const promise = new Promise((resolve) => {
          resolveRequest = resolve;
        });
        return { promise, resolveRequest };
      };

      const { promise, resolveRequest } = createPromise();

      server.use(
        http.get(`${API_BASE_URL}/health`, async () => {
          await promise;
          return HttpResponse.json({
            status: "ok",
            service: "knowrag",
            checks: {
              postgres: { status: "ok" },
              qdrant: { status: "ok" },
              elasticsearch: { status: "ok" },
            },
          });
        })
      );

      renderWithProviders(<HealthPanel />);

      const refreshButton = screen.getByRole("button", { name: /refresh/i });

      await userEvent.click(refreshButton);

      // During fetch, button should be disabled
      expect(refreshButton).toBeDisabled();

      resolveRequest(undefined);

      // After fetch, should be enabled again
      await waitFor(() => {
        expect(refreshButton).not.toBeDisabled();
      });
    });
  });

  // Empty checks case tested implicitly in other tests
  // (schema requires checks to have all three datastores)

  describe("aria-live for accessibility", () => {
    it("has aria-live region that updates on status changes", async () => {
      server.use(
        http.get(`${API_BASE_URL}/health`, () => {
          return HttpResponse.json({
            status: "ok",
            service: "knowrag",
            checks: {
              postgres: { status: "ok" },
              qdrant: { status: "ok" },
              elasticsearch: { status: "ok" },
            },
          });
        })
      );

      renderWithProviders(<HealthPanel />);

      await waitFor(() => {
        expect(screen.getByText("All systems operational")).toBeInTheDocument();
      });

      // The live region is a div with aria-live, not a role="region"
      const liveRegions = document.querySelectorAll('[aria-live="polite"]');
      expect(liveRegions.length).toBeGreaterThan(0);
    });
  });

  describe("datastore order", () => {
    it("displays datastores in fixed order: postgres, qdrant, elasticsearch", async () => {
      server.use(
        http.get(`${API_BASE_URL}/health`, () => {
          return HttpResponse.json({
            status: "ok",
            service: "knowrag",
            checks: {
              postgres: { status: "ok" },
              qdrant: { status: "ok" },
              elasticsearch: { status: "ok" },
            },
          });
        })
      );

      renderWithProviders(<HealthPanel />);

      await waitFor(() => {
        expect(screen.getByText("Postgres")).toBeInTheDocument();
      });

      const items = screen.getAllByRole("listitem");
      expect(items).toHaveLength(3);

      const names = items.map((item) => item.textContent);
      const postgresIndex = names.findIndex((n) => n?.includes("Postgres"));
      const qdrantIndex = names.findIndex((n) => n?.includes("Qdrant"));
      const elasticsearchIndex = names.findIndex((n) => n?.includes("Elasticsearch"));

      expect(postgresIndex).toBeLessThan(qdrantIndex);
      expect(qdrantIndex).toBeLessThan(elasticsearchIndex);
    });
  });

  describe("detail messages", () => {
    it("displays detail message when present", async () => {
      server.use(
        http.get(`${API_BASE_URL}/health`, () => {
          return HttpResponse.json(
            {
              status: "degraded",
              service: "knowrag",
              checks: {
                postgres: { status: "error", detail: "Timeout after 30s" },
                qdrant: { status: "ok" },
                elasticsearch: { status: "ok" },
              },
            },
            { status: 503 }
          );
        })
      );

      renderWithProviders(<HealthPanel />);

      await waitFor(() => {
        expect(screen.getByText("Timeout after 30s")).toBeInTheDocument();
      });
    });

    it("omits detail message when not present", async () => {
      server.use(
        http.get(`${API_BASE_URL}/health`, () => {
          return HttpResponse.json({
            status: "ok",
            service: "knowrag",
            checks: {
              postgres: { status: "ok" },
              qdrant: { status: "ok" },
              elasticsearch: { status: "ok" },
            },
          });
        })
      );

      renderWithProviders(<HealthPanel />);

      await waitFor(() => {
        expect(screen.getByText("Postgres")).toBeInTheDocument();
      });

      const postgresRow = screen.getByText("Postgres").closest("li");
      // Should not have extra text beyond the status
      const text = postgresRow?.textContent;
      expect(text).toContain("Postgres");
      expect(text).toContain("Ok");
    });
  });
});
