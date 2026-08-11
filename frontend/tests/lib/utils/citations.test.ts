import { describe, expect, it } from "vitest";

import {
  parseCitations,
  getUniqueCitationTags,
  type CitationSegment,
} from "@/lib/utils/citations";

describe("citation parsing", () => {
  describe("parseCitations", () => {
    it("parses answer with no tags as single text segment", () => {
      const answer = "RISC uses a reduced instruction set";
      const segments = parseCitations(answer);

      expect(segments).toEqual([
        { type: "text", text: "RISC uses a reduced instruction set" },
      ]);
    });

    it("parses single citation tag", () => {
      const answer = "RISC uses a reduced instruction set [C1]";
      const segments = parseCitations(answer);

      expect(segments).toEqual([
        { type: "text", text: "RISC uses a reduced instruction set " },
        { type: "citation", tag: "C1", index: 1 },
      ]);
    });

    it("parses multiple tags in reading order", () => {
      const answer = "RISC [C1] uses [C3] a reduced set";
      const segments = parseCitations(answer);

      expect(segments).toEqual([
        { type: "text", text: "RISC " },
        { type: "citation", tag: "C1", index: 1 },
        { type: "text", text: " uses " },
        { type: "citation", tag: "C3", index: 3 },
        { type: "text", text: " a reduced set" },
      ]);
    });

    it("handles adjacent tags with no text between", () => {
      const answer = "RISC [C1][C2] architecture";
      const segments = parseCitations(answer);

      expect(segments).toEqual([
        { type: "text", text: "RISC " },
        { type: "citation", tag: "C1", index: 1 },
        { type: "citation", tag: "C2", index: 2 },
        { type: "text", text: " architecture" },
      ]);
    });

    it("handles tag at start of answer", () => {
      const answer = "[C1] RISC is important";
      const segments = parseCitations(answer);

      expect(segments).toEqual([
        { type: "citation", tag: "C1", index: 1 },
        { type: "text", text: " RISC is important" },
      ]);
    });

    it("handles tag at end of answer", () => {
      const answer = "RISC is important [C1]";
      const segments = parseCitations(answer);

      expect(segments).toEqual([
        { type: "text", text: "RISC is important " },
        { type: "citation", tag: "C1", index: 1 },
      ]);
    });

    it("handles only tags with no other text", () => {
      const answer = "[C1][C2]";
      const segments = parseCitations(answer);

      expect(segments).toEqual([
        { type: "citation", tag: "C1", index: 1 },
        { type: "citation", tag: "C2", index: 2 },
      ]);
    });

    it("handles large citation indices", () => {
      const answer = "Something [C123]";
      const segments = parseCitations(answer);

      expect(segments).toEqual([
        { type: "text", text: "Something " },
        { type: "citation", tag: "C123", index: 123 },
      ]);
    });

    it("treats malformed tags as literal text", () => {
      const answer = "Invalid [C] and [Cx] tags";
      const segments = parseCitations(answer);

      // Malformed tags are left as text, not parsed
      expect(segments).toEqual([
        { type: "text", text: "Invalid [C] and [Cx] tags" },
      ]);
    });

    it("requires capital C in citation tag", () => {
      const answer = "Lowercase [c1] tag should not parse";
      const segments = parseCitations(answer);

      expect(segments).toEqual([
        { type: "text", text: "Lowercase [c1] tag should not parse" },
      ]);
    });

    it("requires digits after C", () => {
      const answer = "Missing digits [C] here";
      const segments = parseCitations(answer);

      expect(segments).toEqual([
        { type: "text", text: "Missing digits [C] here" },
      ]);
    });

    it("handles mixed valid and invalid tags", () => {
      const answer = "Valid [C1] and invalid [Cx] and valid [C2]";
      const segments = parseCitations(answer);

      expect(segments).toEqual([
        { type: "text", text: "Valid " },
        { type: "citation", tag: "C1", index: 1 },
        { type: "text", text: " and invalid [Cx] and valid " },
        { type: "citation", tag: "C2", index: 2 },
      ]);
    });

    it("does not parse tags with spaces", () => {
      const answer = "Not a tag [C 1]";
      const segments = parseCitations(answer);

      expect(segments).toEqual([
        { type: "text", text: "Not a tag [C 1]" },
      ]);
    });

    it("handles bracket characters elsewhere in text", () => {
      const answer = "Arrays [0] and maps {key} but citation [C1]";
      const segments = parseCitations(answer);

      expect(segments).toEqual([
        { type: "text", text: "Arrays [0] and maps {key} but citation " },
        { type: "citation", tag: "C1", index: 1 },
      ]);
    });
  });

  describe("getUniqueCitationTags", () => {
    it("returns empty array for text without citations", () => {
      const answer = "No citations here";
      const tags = getUniqueCitationTags(answer);

      expect(tags).toEqual([]);
    });

    it("returns single tag", () => {
      const answer = "Some text [C1]";
      const tags = getUniqueCitationTags(answer);

      expect(tags).toEqual(["C1"]);
    });

    it("returns tags in first-appearance order", () => {
      const answer = "[C3] first [C1] second [C3] again [C2] last";
      const tags = getUniqueCitationTags(answer);

      expect(tags).toEqual(["C3", "C1", "C2"]);
    });

    it("deduplicates repeated tags", () => {
      const answer = "[C1] [C1] [C1]";
      const tags = getUniqueCitationTags(answer);

      expect(tags).toEqual(["C1"]);
    });

    it("preserves order and dedupes in one pass", () => {
      const answer = "[C3] [C1] [C3] [C2] [C1] [C3]";
      const tags = getUniqueCitationTags(answer);

      expect(tags).toEqual(["C3", "C1", "C2"]);
    });

    it("ignores malformed tags", () => {
      const answer = "[C1] [c2] [C] [C3]";
      const tags = getUniqueCitationTags(answer);

      // Only C1 and C3 are valid
      expect(tags).toEqual(["C1", "C3"]);
    });

    it("handles large citation indices", () => {
      const answer = "[C1] [C999] [C1]";
      const tags = getUniqueCitationTags(answer);

      expect(tags).toEqual(["C1", "C999"]);
    });
  });

  describe("integration: parsing then extracting unique tags", () => {
    it("can extract unique tags from parsed segments", () => {
      const answer = "[C1] first part [C2] second [C1] again";
      const segments = parseCitations(answer);
      const tags = getUniqueCitationTags(answer);

      const citationSegments = segments.filter(
        (s): s is Extract<CitationSegment, { type: "citation" }> =>
          s.type === "citation"
      );

      expect(citationSegments).toHaveLength(3);
      expect(tags).toEqual(["C1", "C2"]);
    });
  });

  describe("edge cases", () => {
    it("handles empty string", () => {
      const segments = parseCitations("");
      expect(segments).toEqual([]);

      const tags = getUniqueCitationTags("");
      expect(tags).toEqual([]);
    });

    it("handles string with only whitespace", () => {
      const segments = parseCitations("   ");
      expect(segments).toEqual([{ type: "text", text: "   " }]);
    });

    it("handles very long answer", () => {
      const longText = "word ".repeat(1000);
      const answer = longText + "[C1] " + longText;
      const segments = parseCitations(answer);

      expect(segments.length).toBeGreaterThan(0);
      const citations = segments.filter((s) => s.type === "citation");
      expect(citations).toHaveLength(1);
    });

    it("handles many citation tags", () => {
      let answer = "";
      for (let i = 1; i <= 100; i++) {
        answer += ` [C${i}]`;
      }
      const tags = getUniqueCitationTags(answer);

      expect(tags).toHaveLength(100);
      expect(tags[0]).toBe("C1");
      expect(tags[99]).toBe("C100");
    });
  });
});
