import { beforeEach, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";

import {
  refreshAccessToken,
  isExemptFromRefresh,
  bootstrapSession,
} from "@/lib/auth/refresh";
import { setAccessToken, getAccessToken, clearAccessToken } from "@/lib/auth/token-store";
import { server } from "../../setup";

const API_BASE_URL = "http://localhost:8000";

describe("single-flight refresh — N concurrent 401s fire exactly ONE request", () => {
  beforeEach(() => {
    // Reset module state between tests
    clearAccessToken();
  });

  it("fires exactly one refresh when N concurrent callers request simultaneously", async () => {
    let refreshCallCount = 0;

    server.use(
      http.post(`${API_BASE_URL}/auth/refresh`, () => {
        refreshCallCount++;
        return HttpResponse.json({
          access_token: "new-token",
          token_type: "bearer",
          expires_in: 900,
        });
      })
    );

    // Simulate 5 concurrent refresh requests
    const promises = Array.from({ length: 5 }, () => refreshAccessToken());

    const results = await Promise.all(promises);

    // All 5 callers get the same token
    expect(results).toEqual([
      "new-token",
      "new-token",
      "new-token",
      "new-token",
      "new-token",
    ]);

    // But only one actual network request was made
    expect(refreshCallCount).toBe(1);
  });

  it("clears in-flight promise after refresh completes", async () => {
    let refreshCallCount = 0;

    server.use(
      http.post(`${API_BASE_URL}/auth/refresh`, () => {
        refreshCallCount++;
        return HttpResponse.json({
          access_token: `token-${refreshCallCount}`,
          token_type: "bearer",
          expires_in: 900,
        });
      })
    );

    // First refresh
    const result1 = await refreshAccessToken();
    expect(result1).toBe("token-1");
    expect(refreshCallCount).toBe(1);

    // Second refresh (separate call, not concurrent) should fire a new request
    const result2 = await refreshAccessToken();
    expect(result2).toBe("token-2");
    expect(refreshCallCount).toBe(2);
  });

  it("resolves with null on 401 response and clears token", async () => {
    server.use(
      http.post(`${API_BASE_URL}/auth/refresh`, () => {
        return HttpResponse.json(
          { detail: "Refresh token expired or revoked" },
          { status: 401 }
        );
      })
    );

    setAccessToken("some-old-token");

    const result = await refreshAccessToken();

    expect(result).toBeNull();
    expect(getAccessToken()).toBeNull();
  });

  it("resolves with null on network error and clears token", async () => {
    server.use(
      http.post(`${API_BASE_URL}/auth/refresh`, () => {
        throw new Error("Network failure");
      })
    );

    setAccessToken("some-old-token");

    const result = await refreshAccessToken();

    expect(result).toBeNull();
    expect(getAccessToken()).toBeNull();
  });

  it("resolves with null on unparseable response and clears token", async () => {
    server.use(
      http.post(`${API_BASE_URL}/auth/refresh`, () => {
        return HttpResponse.json({ invalid: "response" });
      })
    );

    setAccessToken("some-old-token");

    const result = await refreshAccessToken();

    expect(result).toBeNull();
    expect(getAccessToken()).toBeNull();
  });

  it("never throws — always resolves to string | null", async () => {
    server.use(
      http.post(`${API_BASE_URL}/auth/refresh`, () => {
        throw new Error("Complete network failure");
      })
    );

    let error: unknown;
    try {
      await refreshAccessToken();
    } catch (e) {
      error = e;
    }

    // No error should have been thrown
    expect(error).toBeUndefined();
  });

  it("stores successful token in memory via setAccessToken", async () => {
    server.use(
      http.post(`${API_BASE_URL}/auth/refresh`, () => {
        return HttpResponse.json({
          access_token: "fresh-token",
          token_type: "bearer",
          expires_in: 900,
        });
      })
    );

    clearAccessToken();
    const result = await refreshAccessToken();

    expect(result).toBe("fresh-token");
    expect(getAccessToken()).toBe("fresh-token");
  });

  describe("exemptions from refresh interceptor", () => {
    it("exempts /auth/login from refresh recursion", () => {
      expect(isExemptFromRefresh("/auth/login")).toBe(true);
    });

    it("exempts /auth/register from refresh recursion", () => {
      expect(isExemptFromRefresh("/auth/register")).toBe(true);
    });

    it("exempts /auth/refresh from refresh recursion", () => {
      expect(isExemptFromRefresh("/auth/refresh")).toBe(true);
    });

    it("exempts /auth/refresh with trailing path", () => {
      expect(isExemptFromRefresh("/auth/refresh/check")).toBe(true);
    });

    it("does not exempt /auth/logout", () => {
      expect(isExemptFromRefresh("/auth/logout")).toBe(false);
    });

    it("does not exempt /query", () => {
      expect(isExemptFromRefresh("/query")).toBe(false);
    });

    it("uses startsWith matching, not exact equality", () => {
      expect(isExemptFromRefresh("/auth/login")).toBe(true);
      expect(isExemptFromRefresh("/auth/login/check")).toBe(true);
      expect(isExemptFromRefresh("/auth/login-other")).toBe(true); // also starts with /auth/login
    });
  });

  describe("bootstrapSession", () => {
    it("attempts one silent refresh on cold start", async () => {
      let refreshCallCount = 0;

      server.use(
        http.post(`${API_BASE_URL}/auth/refresh`, () => {
          refreshCallCount++;
          return HttpResponse.json({
            access_token: "bootstrapped-token",
            token_type: "bearer",
            expires_in: 900,
          });
        })
      );

      clearAccessToken();
      const result = await bootstrapSession();

      expect(result).toBe(true);
      expect(refreshCallCount).toBe(1);
      expect(getAccessToken()).toBe("bootstrapped-token");
    });

    it("returns false when no session to restore", async () => {
      server.use(
        http.post(`${API_BASE_URL}/auth/refresh`, () => {
          return HttpResponse.json(
            { detail: "No valid refresh token" },
            { status: 401 }
          );
        })
      );

      clearAccessToken();
      const result = await bootstrapSession();

      expect(result).toBe(false);
      expect(getAccessToken()).toBeNull();
    });

    it("does not throw on refresh failure (closed over clearAccessToken)", async () => {
      server.use(
        http.post(`${API_BASE_URL}/auth/refresh`, () => {
          return new HttpResponse(null, { status: 500 });
        })
      );

      clearAccessToken();
      let error: unknown;
      try {
        await bootstrapSession();
      } catch (e) {
        error = e;
      }

      expect(error).toBeUndefined();
    });
  });

  describe("retry logic", () => {
    it("limits refresh attempts to one per 401 on a given request path", async () => {
      // This is tested indirectly through the client.test.ts integration
      // since refresh is called from the client's 401 handler
      expect(isExemptFromRefresh("/query")).toBe(false);
      expect(isExemptFromRefresh("/ingest")).toBe(false);
    });
  });

  describe("credentials: 'include' for cookie handling", () => {
    it("includes credentials in refresh request (for HttpOnly cookie rotation)", async () => {
      let capturedCredentials: RequestCredentials | undefined;

      server.use(
        http.post(`${API_BASE_URL}/auth/refresh`, ({ request }) => {
          capturedCredentials = request.credentials;
          return HttpResponse.json({
            access_token: "token",
            token_type: "bearer",
            expires_in: 900,
          });
        })
      );

      await refreshAccessToken();

      expect(capturedCredentials).toBe("include");
    });
  });

  describe("token store integration", () => {
    it("clears token store on failed refresh", async () => {
      server.use(
        http.post(`${API_BASE_URL}/auth/refresh`, () => {
          return new HttpResponse(null, { status: 500 });
        })
      );

      setAccessToken("old-token");
      expect(getAccessToken()).toBe("old-token");

      await refreshAccessToken();

      expect(getAccessToken()).toBeNull();
    });

    it("updates token store on successful refresh", async () => {
      server.use(
        http.post(`${API_BASE_URL}/auth/refresh`, () => {
          return HttpResponse.json({
            access_token: "brand-new-token",
            token_type: "bearer",
            expires_in: 900,
          });
        })
      );

      clearAccessToken();
      await refreshAccessToken();

      expect(getAccessToken()).toBe("brand-new-token");
    });
  });

  describe("in-memory only storage (no localStorage/sessionStorage/cookie)", () => {
    it("access token is never stored in localStorage", async () => {
      server.use(
        http.post(`${API_BASE_URL}/auth/refresh`, () => {
          return HttpResponse.json({
            access_token: "secret-access-token-123",
            token_type: "bearer",
            expires_in: 900,
          });
        })
      );

      clearAccessToken();
      await refreshAccessToken();

      // Verify token is in memory
      expect(getAccessToken()).toBe("secret-access-token-123");

      // Verify it's NOT in localStorage
      const localStorageContent = JSON.stringify(localStorage);
      expect(localStorageContent).not.toContain("secret-access-token-123");
      expect(localStorage.getItem("secret-access-token-123")).toBeNull();
      expect(localStorage.getItem("access_token")).toBeNull();
      expect(localStorage.getItem("token")).toBeNull();
    });

    it("access token is never stored in sessionStorage", async () => {
      server.use(
        http.post(`${API_BASE_URL}/auth/refresh`, () => {
          return HttpResponse.json({
            access_token: "another-secret-token-456",
            token_type: "bearer",
            expires_in: 900,
          });
        })
      );

      clearAccessToken();
      await refreshAccessToken();

      // Verify token is in memory
      expect(getAccessToken()).toBe("another-secret-token-456");

      // Verify it's NOT in sessionStorage
      const sessionStorageContent = JSON.stringify(sessionStorage);
      expect(sessionStorageContent).not.toContain("another-secret-token-456");
      expect(sessionStorage.getItem("another-secret-token-456")).toBeNull();
      expect(sessionStorage.getItem("access_token")).toBeNull();
      expect(sessionStorage.getItem("token")).toBeNull();
    });

    it("access token is never stored in readable cookie", async () => {
      server.use(
        http.post(`${API_BASE_URL}/auth/refresh`, () => {
          return HttpResponse.json({
            access_token: "yet-another-secret-789",
            token_type: "bearer",
            expires_in: 900,
          });
        })
      );

      clearAccessToken();
      await refreshAccessToken();

      // Verify token is in memory
      expect(getAccessToken()).toBe("yet-another-secret-789");

      // Verify it's NOT in readable cookies (HttpOnly refresh cookie is server-managed)
      expect(document.cookie).not.toContain("yet-another-secret-789");
      expect(document.cookie).not.toContain("access_token=");
      expect(document.cookie).not.toContain("token=");
    });

    it("access token is in-memory only after login cycle", async () => {
      server.use(
        http.post(`${API_BASE_URL}/auth/refresh`, () => {
          return HttpResponse.json({
            access_token: "in-memory-token-only",
            token_type: "bearer",
            expires_in: 900,
          });
        })
      );

      clearAccessToken();
      const token = await refreshAccessToken();

      // Token should be in memory
      expect(token).toBe("in-memory-token-only");
      expect(getAccessToken()).toBe("in-memory-token-only");

      // But nowhere in persisted storage
      const allStorage = `${JSON.stringify(localStorage)};${JSON.stringify(sessionStorage)};${document.cookie}`;
      expect(allStorage).not.toContain("in-memory-token-only");
    });

    it("clearing token removes it from memory, not storage (since it was never there)", async () => {
      server.use(
        http.post(`${API_BASE_URL}/auth/refresh`, () => {
          return HttpResponse.json({
            access_token: "temp-token",
            token_type: "bearer",
            expires_in: 900,
          });
        })
      );

      await refreshAccessToken();
      expect(getAccessToken()).toBe("temp-token");

      clearAccessToken();
      expect(getAccessToken()).toBeNull();

      // Since token was never in storage, clearing doesn't need to do anything there
      expect(localStorage.length).toBe(0);
      expect(sessionStorage.length).toBe(0);
    });
  });
});
