import { describe, expect, it } from "vitest";

import {
  isSafeRedirectTarget,
  buildLoginHref,
  resolveNextParam,
} from "@/components/auth/redirect";

describe("redirect.ts — open-redirect guard", () => {
  describe("isSafeRedirectTarget", () => {
    describe("accepts same-origin paths", () => {
      it("accepts absolute paths starting with /", () => {
        expect(isSafeRedirectTarget("/")).toBe(true);
        expect(isSafeRedirectTarget("/home")).toBe(true);
        expect(isSafeRedirectTarget("/documents")).toBe(true);
        expect(isSafeRedirectTarget("/path/to/page")).toBe(true);
      });

      it("accepts paths with query strings", () => {
        expect(isSafeRedirectTarget("/page?key=value")).toBe(true);
        expect(isSafeRedirectTarget("/?next=%2Fprevious")).toBe(true);
      });

      it("accepts paths with fragments", () => {
        expect(isSafeRedirectTarget("/#section")).toBe(true);
        expect(isSafeRedirectTarget("/page#top")).toBe(true);
      });

      it("accepts paths with special characters", () => {
        expect(isSafeRedirectTarget("/search?q=hello%20world")).toBe(true);
        expect(isSafeRedirectTarget("/user%2Fprofile")).toBe(true);
      });
    });

    describe("rejects absolute URLs", () => {
      it("rejects http URLs", () => {
        expect(isSafeRedirectTarget("http://localhost:3000/")).toBe(false);
        expect(isSafeRedirectTarget("http://example.com")).toBe(false);
      });

      it("rejects https URLs", () => {
        expect(isSafeRedirectTarget("https://evil.com")).toBe(false);
        expect(isSafeRedirectTarget("https://localhost:3000/home")).toBe(false);
      });

      it("rejects localhost URLs", () => {
        expect(isSafeRedirectTarget("localhost:3000")).toBe(false);
        expect(isSafeRedirectTarget("localhost:3000/")).toBe(false);
      });
    });

    describe("rejects protocol-relative URLs", () => {
      it("rejects // URLs", () => {
        expect(isSafeRedirectTarget("//evil.com")).toBe(false);
        expect(isSafeRedirectTarget("//attacker.com/path")).toBe(false);
      });

      it("rejects /\\ URLs (backslash form)", () => {
        expect(isSafeRedirectTarget("/\\evil.com")).toBe(false);
        expect(isSafeRedirectTarget("/\\attacker.com/path")).toBe(false);
      });
    });

    describe("rejects invalid inputs", () => {
      it("rejects null", () => {
        expect(isSafeRedirectTarget(null)).toBe(false);
      });

      it("rejects undefined", () => {
        expect(isSafeRedirectTarget(undefined)).toBe(false);
      });

      it("rejects empty string", () => {
        expect(isSafeRedirectTarget("")).toBe(false);
      });

      it("rejects relative paths without leading /", () => {
        expect(isSafeRedirectTarget("home")).toBe(false);
        expect(isSafeRedirectTarget("path/to/page")).toBe(false);
      });

      it("rejects paths starting with space", () => {
        expect(isSafeRedirectTarget(" /home")).toBe(false);
      });

      it("rejects javascript: URLs", () => {
        expect(isSafeRedirectTarget("javascript:alert('xss')")).toBe(false);
      });

      it("rejects data: URLs", () => {
        expect(isSafeRedirectTarget("data:text/html,<script>alert('xss')</script>")).toBe(
          false
        );
      });
    });

    describe("type guard behavior", () => {
      it("acts as a type guard returning string when true", () => {
        const value: string | null | undefined = "/home";
        if (isSafeRedirectTarget(value)) {
          // value is now typed as string, not string | null | undefined
          const path: string = value;
          expect(path).toBe("/home");
        }
      });

      it("type guard fails for null", () => {
        const value: string | null = null;
        const isSafe = isSafeRedirectTarget(value);
        expect(isSafe).toBe(false);
      });
    });
  });

  describe("buildLoginHref", () => {
    it("builds plain /login for default route", () => {
      expect(buildLoginHref("/")).toBe("/login");
    });

    it("builds /login with next param for other paths", () => {
      expect(buildLoginHref("/documents")).toBe("/login?next=%2Fdocuments");
      expect(buildLoginHref("/history")).toBe("/login?next=%2Fhistory");
    });

    it("encodes special characters in path", () => {
      expect(buildLoginHref("/search?q=hello world")).toBe(
        "/login?next=%2Fsearch%3Fq%3Dhello%20world"
      );
    });

    it("returns plain /login for unsafe paths (open redirect protection)", () => {
      // Unsafe paths should not get encoded into next param
      expect(buildLoginHref("//evil.com")).toBe("/login");
      expect(buildLoginHref("https://evil.com")).toBe("/login");
      expect(buildLoginHref("javascript:alert('xss')")).toBe("/login");
    });

    it("handles paths with fragments", () => {
      expect(buildLoginHref("/page#section")).toBe("/login?next=%2Fpage%23section");
    });

    it("handles paths with query strings", () => {
      expect(buildLoginHref("/search?q=test")).toBe("/login?next=%2Fsearch%3Fq%3Dtest");
    });
  });

  describe("resolveNextParam", () => {
    it("extracts safe next parameter", () => {
      const params = new URLSearchParams("next=%2Fdocuments");
      expect(resolveNextParam(params)).toBe("/documents");
    });

    it("returns default / when next is missing", () => {
      const params = new URLSearchParams();
      expect(resolveNextParam(params)).toBe("/");
    });

    it("returns default / when next is null", () => {
      const params = new URLSearchParams("other=value");
      expect(resolveNextParam(params)).toBe("/");
    });

    it("returns default / when next is unsafe", () => {
      const params = new URLSearchParams("next=https%3A%2F%2Fevil.com");
      expect(resolveNextParam(params)).toBe("/");
    });

    it("rejects protocol-relative URLs in next", () => {
      const params = new URLSearchParams("next=%2F%2Fevil.com");
      expect(resolveNextParam(params)).toBe("/");
    });

    it("handles URL-encoded paths correctly", () => {
      const params = new URLSearchParams("next=%2Fdocuments%3Fid%3D123");
      expect(resolveNextParam(params)).toBe("/documents?id=123");
    });

    it("handles multiple params gracefully", () => {
      const params = new URLSearchParams("other=value&next=%2Fhome&more=stuff");
      expect(resolveNextParam(params)).toBe("/home");
    });

    it("validates parsed value before returning", () => {
      // Even if next looks reasonable, verify it passes isSafeRedirectTarget
      const params = new URLSearchParams("next=evil.com"); // no leading /
      expect(resolveNextParam(params)).toBe("/");
    });
  });

  describe("security properties", () => {
    describe("prevents open redirects", () => {
      it("prevents redirect to attacker site via HTTP", () => {
        const loginHref = buildLoginHref("http://attacker.com");
        expect(loginHref).toBe("/login"); // no next param
      });

      it("prevents redirect to attacker site via HTTPS", () => {
        const loginHref = buildLoginHref("https://attacker.com");
        expect(loginHref).toBe("/login");
      });

      it("prevents protocol-relative redirect", () => {
        const loginHref = buildLoginHref("//attacker.com/phishing");
        expect(loginHref).toBe("/login");
      });

      it("prevents backslash-prefixed protocol-relative redirect", () => {
        const loginHref = buildLoginHref("/\\attacker.com");
        expect(loginHref).toBe("/login");
      });
    });

    describe("prevents XSS via URL", () => {
      it("prevents javascript: execution", () => {
        expect(isSafeRedirectTarget("javascript:alert('xss')")).toBe(false);
      });

      it("prevents data: URLs", () => {
        expect(isSafeRedirectTarget("data:text/html,<img src=x onerror='alert(1)'>")).toBe(
          false
        );
      });
    });

    describe("handles edge cases", () => {
      it("allows /path starting with many slashes when safe", () => {
        // A path like //multiple/slashes is different from protocol-relative
        // It's ambiguous and should be rejected
        expect(isSafeRedirectTarget("//multiple/slashes")).toBe(false);
      });

      it("allows /path with encoded slashes", () => {
        expect(isSafeRedirectTarget("/%2F/path")).toBe(true); // /encoded-slash/path is valid
      });

      it("allows paths with encoded dangerous characters", () => {
        // Encoded characters in the path are fine; they're not interpreted
        expect(isSafeRedirectTarget("/%3Cscript%3E")).toBe(true);
      });
    });
  });
});
