import { describe, expect, it } from "vitest";

import {
  formatChunkKey,
  parseChunkKey,
  type ChunkKey,
} from "@/lib/utils/chunk-key";

describe("chunk-key parsing and formatting", () => {
  describe("formatChunkKey", () => {
    it("builds canonical string from documentId and chunkIndex", () => {
      expect(formatChunkKey(1, 3)).toBe("1:3");
      expect(formatChunkKey(42, 0)).toBe("42:0");
      expect(formatChunkKey(999, 2343)).toBe("999:2343");
    });

    it("handles large numbers", () => {
      expect(formatChunkKey(1000000, 999999)).toBe("1000000:999999");
    });
  });

  describe("parseChunkKey", () => {
    it("round-trips with formatChunkKey", () => {
      const key = formatChunkKey(1, 3);
      const parsed = parseChunkKey(key);
      expect(parsed).toEqual({ documentId: 1, chunkIndex: 3 });
    });

    it("parses valid chunk key", () => {
      const result = parseChunkKey("1:3");
      expect(result).toEqual({
        documentId: 1,
        chunkIndex: 3,
      });
    });

    it("parses zero values", () => {
      const result = parseChunkKey("0:0");
      expect(result).toEqual({
        documentId: 0,
        chunkIndex: 0,
      });
    });

    it("parses large numbers", () => {
      const result = parseChunkKey("1000000:999999");
      expect(result).toEqual({
        documentId: 1000000,
        chunkIndex: 999999,
      });
    });
  });

  describe("malformed input returns null", () => {
    it("returns null for empty string", () => {
      expect(parseChunkKey("")).toBeNull();
    });

    it("returns null for single number (no separator)", () => {
      expect(parseChunkKey("123")).toBeNull();
    });

    it("returns null for missing documentId", () => {
      expect(parseChunkKey(":3")).toBeNull();
    });

    it("returns null for missing chunkIndex", () => {
      expect(parseChunkKey("1:")).toBeNull();
    });

    it("returns null for multiple separators", () => {
      expect(parseChunkKey("1:2:3")).toBeNull();
    });

    it("returns null for non-numeric parts", () => {
      expect(parseChunkKey("abc:def")).toBeNull();
    });

    it("returns null for float numbers", () => {
      expect(parseChunkKey("1.5:3.2")).toBeNull();
    });

    it("returns null for negative numbers", () => {
      expect(parseChunkKey("-1:3")).toBeNull();
      expect(parseChunkKey("1:-3")).toBeNull();
    });

    it("returns null for whitespace-containing strings", () => {
      expect(parseChunkKey("1 :3")).toBeNull();
      expect(parseChunkKey("1: 3")).toBeNull();
    });

    it("handles leading/trailing whitespace by trimming first", () => {
      expect(parseChunkKey("  1:3  ")).toEqual({
        documentId: 1,
        chunkIndex: 3,
      });
    });

    it("returns null for non-safe-integer values", () => {
      // Number.MAX_SAFE_INTEGER is 9007199254740991
      // Test beyond that
      expect(parseChunkKey("9007199254740992:3")).toBeNull();
      expect(parseChunkKey("1:9007199254740992")).toBeNull();
    });

    it("returns null for other malformed inputs", () => {
      expect(parseChunkKey("1|3")).toBeNull();
      expect(parseChunkKey("1,3")).toBeNull();
      expect(parseChunkKey("1/3")).toBeNull();
      expect(parseChunkKey("chunk:1:3")).toBeNull();
    });
  });

  describe("integration: parsing from wire, then using parsed values", () => {
    it("can use parsed values to rebuild original key", () => {
      const original = "42:17";
      const parsed = parseChunkKey(original);
      expect(parsed).not.toBeNull();

      if (parsed) {
        const rebuilt = formatChunkKey(parsed.documentId, parsed.chunkIndex);
        expect(rebuilt).toBe(original);
      }
    });

    it("degrades gracefully on corrupt key", () => {
      const corruptKey = "not-a-valid-key";
      const parsed = parseChunkKey(corruptKey);
      expect(parsed).toBeNull();
      // Component using this would check for null and render a fallback
    });
  });

  describe("type safety", () => {
    it("returns strongly-typed ChunkKey object or null", () => {
      const result: ChunkKey | null = parseChunkKey("1:3");
      if (result !== null) {
        const documentId: number = result.documentId;
        const chunkIndex: number = result.chunkIndex;
        expect(typeof documentId).toBe("number");
        expect(typeof chunkIndex).toBe("number");
      }
    });
  });
});
