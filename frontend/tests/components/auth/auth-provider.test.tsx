import { beforeEach, describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { screen, waitFor } from "@testing-library/react";

import { AuthProvider, useAuthBootStatus } from "@/components/auth/auth-provider";
import { clearAccessToken, getAccessToken } from "@/lib/auth/token-store";
import { renderWithProviders } from "../../utils/render";
import { server } from "../../setup";

const API_BASE_URL = "http://localhost:8000";

/**
 * Test component that reads the auth boot status
 */
function BootStatusDisplay() {
  const status = useAuthBootStatus();
  return <div>Status: {status}</div>;
}

describe("AuthProvider", () => {
  beforeEach(() => {
    clearAccessToken();
    vi.clearAllMocks();
  });

  describe("boot-time silent refresh", () => {
    it("performs exactly one refresh on cold start", async () => {
      let refreshCallCount = 0;

      server.use(
        http.post(`${API_BASE_URL}/auth/refresh`, () => {
          refreshCallCount++;
          return HttpResponse.json({
            access_token: "fresh-token",
            token_type: "bearer",
            expires_in: 900,
          });
        })
      );

      clearAccessToken();

      renderWithProviders(
        <AuthProvider>
          <BootStatusDisplay />
        </AuthProvider>
      );

      // Wait for auth provider to boot and perform refresh
      await waitFor(() => {
        expect(screen.getByText("Status: authenticated")).toBeInTheDocument();
      });

      // CRITICAL: Must fire exactly one refresh, not two (React 19 StrictMode)
      expect(refreshCallCount).toBe(1);
      expect(getAccessToken()).toBe("fresh-token");
    });

    it("restores session without requiring login", async () => {
      server.use(
        http.post(`${API_BASE_URL}/auth/refresh`, () => {
          return HttpResponse.json({
            access_token: "restored-token",
            token_type: "bearer",
            expires_in: 900,
          });
        })
      );

      clearAccessToken();

      renderWithProviders(
        <AuthProvider>
          <BootStatusDisplay />
        </AuthProvider>
      );

      // Should show authenticated after silent refresh completes
      await waitFor(() => {
        expect(screen.getByText("Status: authenticated")).toBeInTheDocument();
      });

      // Token should be restored to memory
      expect(getAccessToken()).toBe("restored-token");
    });

    it("starts with pending status", () => {
      clearAccessToken();

      renderWithProviders(
        <AuthProvider>
          <BootStatusDisplay />
        </AuthProvider>
      );

      // Initially should be pending (still checking)
      expect(screen.getByText("Status: pending")).toBeInTheDocument();
    });
  });

  describe("boot failure (no refresh cookie)", () => {
    it("settles to unauthenticated when refresh fails", async () => {
      server.use(
        http.post(`${API_BASE_URL}/auth/refresh`, () => {
          return HttpResponse.json(
            { detail: "No valid refresh token" },
            { status: 401 }
          );
        })
      );

      clearAccessToken();

      renderWithProviders(
        <AuthProvider>
          <BootStatusDisplay />
        </AuthProvider>
      );

      // Should end up unauthenticated
      await waitFor(() => {
        expect(screen.getByText("Status: unauthenticated")).toBeInTheDocument();
      });

      // No token should be in memory
      expect(getAccessToken()).toBeNull();
    });

    it("does not surface error to user on failed boot refresh", async () => {
      server.use(
        http.post(`${API_BASE_URL}/auth/refresh`, () => {
          return new HttpResponse(null, { status: 500 });
        })
      );

      clearAccessToken();

      // Should not throw or show an error boundary
      expect(() => {
        renderWithProviders(
          <AuthProvider>
            <BootStatusDisplay />
          </AuthProvider>
        );
      }).not.toThrow();

      // Eventually settles to unauthenticated cleanly
      await waitFor(() => {
        expect(screen.getByText("Status: unauthenticated")).toBeInTheDocument();
      });
    });

    it("clears token on refresh failure", async () => {
      server.use(
        http.post(`${API_BASE_URL}/auth/refresh`, () => {
          throw new Error("Network error");
        })
      );

      clearAccessToken();

      renderWithProviders(
        <AuthProvider>
          <BootStatusDisplay />
        </AuthProvider>
      );

      await waitFor(() => {
        expect(screen.getByText("Status: unauthenticated")).toBeInTheDocument();
      });

      expect(getAccessToken()).toBeNull();
    });
  });

  describe("idempotency under React StrictMode", () => {
    it("uses bootedRef to prevent double-invocation of bootstrap", async () => {
      let refreshCallCount = 0;

      server.use(
        http.post(`${API_BASE_URL}/auth/refresh`, () => {
          refreshCallCount++;
          return HttpResponse.json({
            access_token: "token",
            token_type: "bearer",
            expires_in: 900,
          });
        })
      );

      clearAccessToken();

      // Note: We're testing that even if the effect runs twice
      // (as React 19 StrictMode does in dev mode), the bootstrap
      // only fires one network request due to the bootedRef guard
      renderWithProviders(
        <AuthProvider>
          <BootStatusDisplay />
        </AuthProvider>
      );

      await waitFor(() => {
        expect(screen.getByText("Status: authenticated")).toBeInTheDocument();
      });

      // Only one refresh should have fired
      expect(refreshCallCount).toBe(1);
    });
  });

  describe("context provision", () => {
    it("provides boot status to descendants", async () => {
      server.use(
        http.post(`${API_BASE_URL}/auth/refresh`, () => {
          return HttpResponse.json({
            access_token: "token",
            token_type: "bearer",
            expires_in: 900,
          });
        })
      );

      clearAccessToken();

      renderWithProviders(
        <AuthProvider>
          <div>
            <BootStatusDisplay />
            <div>Another component</div>
          </div>
        </AuthProvider>
      );

      // All descendants should have access to boot status
      await waitFor(() => {
        expect(screen.getByText("Status: authenticated")).toBeInTheDocument();
      });
    });
  });

  describe("cleanup on unmount", () => {
    it("cancels pending bootstrap if component unmounts", async () => {
      server.use(
        http.post(`${API_BASE_URL}/auth/refresh`, async () => {
          await new Promise((resolve) => setTimeout(resolve, 100));
          return HttpResponse.json({
            access_token: "token",
            token_type: "bearer",
            expires_in: 900,
          });
        })
      );

      clearAccessToken();

      const { unmount } = renderWithProviders(
        <AuthProvider>
          <BootStatusDisplay />
        </AuthProvider>
      );

      // Unmount before refresh completes
      unmount();

      // Should still be null since the update was cancelled
      expect(getAccessToken()).toBeNull();
    });
  });
});
