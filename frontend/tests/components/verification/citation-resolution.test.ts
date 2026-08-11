import { describe, expect, it } from "vitest";
import { resolveAnswerCitation, zipClaimCitations } from "@/components/verification/citation-resolution";
import type { ClaimVerdict } from "@/types/api";

describe("citation-resolution", () => {
  describe("resolveAnswerCitation", () => {
    it("resolves valid citation tag C1 to first chunk", () => {
      const retrieved = ["1:3", "1:5", "1:6"];
      const result = resolveAnswerCitation("C1", retrieved);
      expect(result).toBe("1:3");
    });

    it("resolves C3 to third chunk", () => {
      const retrieved = ["1:3", "1:5", "1:6"];
      const result = resolveAnswerCitation("C3", retrieved);
      expect(result).toBe("1:6");
    });

    it("returns null for tag beyond retrieved chunks", () => {
      const retrieved = ["1:3", "1:5"];
      const result = resolveAnswerCitation("C5", retrieved);
      expect(result).toBeNull();
    });

    it("returns null for malformed tag without C prefix", () => {
      const retrieved = ["1:3"];
      expect(resolveAnswerCitation("1", retrieved)).toBeNull();
      expect(resolveAnswerCitation("C", retrieved)).toBeNull();
      expect(resolveAnswerCitation("CX", retrieved)).toBeNull();
    });

    it("returns null for non-numeric tag", () => {
      const retrieved = ["1:3"];
      expect(resolveAnswerCitation("CITE", retrieved)).toBeNull();
    });

    it("returns null for C0 (out of range)", () => {
      const retrieved = ["1:3"];
      expect(resolveAnswerCitation("C0", retrieved)).toBeNull();
    });

    it("returns null for empty retrieved list", () => {
      expect(resolveAnswerCitation("C1", [])).toBeNull();
    });
  });

  describe("zipClaimCitations", () => {
    it("zips matching citations and chunk_ids arrays", () => {
      const claim: Pick<ClaimVerdict, "citations" | "chunk_ids"> = {
        citations: ["C1", "C2"],
        chunk_ids: ["1:3", "1:5"],
      };

      const result = zipClaimCitations(claim);
      expect(result).toEqual([
        { tag: "C1", chunkId: "1:3" },
        { tag: "C2", chunkId: "1:5" },
      ]);
    });

    it("degrades gracefully when citations array is longer than chunk_ids", () => {
      const claim: Pick<ClaimVerdict, "citations" | "chunk_ids"> = {
        citations: ["C1", "C2", "C3"],
        chunk_ids: ["1:3", "1:5"],
      };

      const result = zipClaimCitations(claim);
      // Zips only to the shorter length
      expect(result).toHaveLength(2);
      expect(result).toEqual([
        { tag: "C1", chunkId: "1:3" },
        { tag: "C2", chunkId: "1:5" },
      ]);
    });

    it("degrades gracefully when chunk_ids array is longer than citations", () => {
      const claim: Pick<ClaimVerdict, "citations" | "chunk_ids"> = {
        citations: ["C1"],
        chunk_ids: ["1:3", "1:5", "1:6"],
      };

      const result = zipClaimCitations(claim);
      // Zips only to the shorter length
      expect(result).toHaveLength(1);
      expect(result).toEqual([{ tag: "C1", chunkId: "1:3" }]);
    });

    it("handles empty arrays", () => {
      const claim: Pick<ClaimVerdict, "citations" | "chunk_ids"> = {
        citations: [],
        chunk_ids: [],
      };

      const result = zipClaimCitations(claim);
      expect(result).toEqual([]);
    });

    it("skips undefined entries without crashing", () => {
      // Create an array-like object with sparse entries
      const claim: Pick<ClaimVerdict, "citations" | "chunk_ids"> = {
        citations: ["C1", undefined as unknown as string, "C3"],
        chunk_ids: ["1:3", "1:5", "1:6"],
      };

      const result = zipClaimCitations(claim);
      // Should safely skip the undefined entry
      expect(result.length).toBeGreaterThan(0);
      // Should only include valid entries
      expect(result.every((r) => r.tag && r.chunkId)).toBe(true);
    });
  });
});
