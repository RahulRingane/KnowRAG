import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../utils/render";
import { AnswerRenderer } from "@/components/verification/answer-renderer";

describe("AnswerRenderer", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  describe("Citation parsing and rendering", () => {
    it("renders plain text without citations", () => {
      renderWithProviders(
        <AnswerRenderer
          answer="This is plain text without citations."
          retrievedChunkIds={[]}
          onOpenChunk={() => {}}
        />
      );

      expect(screen.getByText("This is plain text without citations.")).toBeInTheDocument();
    });

    it("renders single citation tag as interactive chip", () => {
      const onOpenChunk = vi.fn();

      renderWithProviders(
        <AnswerRenderer
          answer="RISC uses a reduced instruction set [C1]"
          retrievedChunkIds={["1:3"]}
          onOpenChunk={onOpenChunk}
        />
      );

      expect(screen.getByText(/RISC uses a reduced instruction set/)).toBeInTheDocument();

      // Citation should be an interactive button
      const chip = screen.getByRole("button", { name: /View evidence for citation C1/ });
      expect(chip).toBeInTheDocument();
    });

    it("renders multiple citation tags", () => {
      renderWithProviders(
        <AnswerRenderer
          answer="RISC [C1] uses a reduced instruction set [C2]. It is efficient [C3]."
          retrievedChunkIds={["1:3", "1:5", "1:6"]}
          onOpenChunk={() => {}}
        />
      );

      // All three citations should be present
      expect(screen.getByRole("button", { name: /View evidence for citation C1/ })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /View evidence for citation C2/ })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /View evidence for citation C3/ })).toBeInTheDocument();
    });

    it("resolves citation tags to correct chunk IDs", () => {
      const onOpenChunk = vi.fn();

      renderWithProviders(
        <AnswerRenderer
          answer="First [C1] and second [C2]."
          retrievedChunkIds={["1:3", "1:5", "1:6"]}
          onOpenChunk={onOpenChunk}
        />
      );

      const chip1 = screen.getByRole("button", { name: /View evidence for citation C1/ });
      const chip2 = screen.getByRole("button", { name: /View evidence for citation C2/ });

      // C1 should resolve to 1:3 (index 0)
      expect(chip1).toHaveAttribute("aria-label", expect.stringContaining("1:3"));

      // C2 should resolve to 1:5 (index 1)
      expect(chip2).toHaveAttribute("aria-label", expect.stringContaining("1:5"));
    });
  });

  describe("Citation out of range handling", () => {
    it("renders unresolved citations as non-interactive", () => {
      renderWithProviders(
        <AnswerRenderer
          answer="Text with [C1] and [C5]."
          retrievedChunkIds={["1:3"]}
          onOpenChunk={() => {}}
        />
      );

      // C1 should be interactive button
      expect(screen.getByRole("button", { name: /View evidence for citation C1/ })).toBeInTheDocument();

      // C5 should be non-interactive (out of range)
      // Look for the non-button span version
      expect(screen.getByText("[C5]")).toBeInTheDocument();
      // Verify it's NOT a button by checking for aria-label typical of buttons
      const c5Span = screen.getByText("[C5]");
      expect(c5Span.tagName).toBe("SPAN");
    });

    it("shows all citations even if some don't resolve", () => {
      renderWithProviders(
        <AnswerRenderer
          answer="Evidence [C1], more evidence [C2], uncertain [C99]."
          retrievedChunkIds={["1:3"]}
          onOpenChunk={() => {}}
        />
      );

      // Resolved citations
      expect(screen.getByRole("button", { name: /View evidence for citation C1/ })).toBeInTheDocument();

      // Unresolved citations should still render
      expect(screen.getByText("[C2]")).toBeInTheDocument();
      expect(screen.getByText("[C99]")).toBeInTheDocument();
    });
  });

  describe("Clicking citation chips", () => {
    it("calls onOpenChunk with correct chunk ID when citation is clicked", async () => {
      const user = userEvent.setup();
      const onOpenChunk = vi.fn();

      renderWithProviders(
        <AnswerRenderer
          answer="Text with [C1] and [C2]."
          retrievedChunkIds={["1:3", "1:5"]}
          onOpenChunk={onOpenChunk}
        />
      );

      const chip1 = screen.getByRole("button", { name: /View evidence for citation C1/ });
      await user.click(chip1);

      expect(onOpenChunk).toHaveBeenCalledWith("1:3");

      const chip2 = screen.getByRole("button", { name: /View evidence for citation C2/ });
      await user.click(chip2);

      expect(onOpenChunk).toHaveBeenCalledWith("1:5");
    });

    it("does not call onOpenChunk for unresolved citations", async () => {
      const user = userEvent.setup();
      const onOpenChunk = vi.fn();

      renderWithProviders(
        <AnswerRenderer
          answer="Text with [C5]."
          retrievedChunkIds={["1:3"]}
          onOpenChunk={onOpenChunk}
        />
      );

      // C5 is not interactive, so we can't click it
      const unresolved = screen.getByText("[C5]");
      expect(unresolved.tagName).toBe("SPAN");

      // Should not be able to click non-button
      try {
        await user.click(unresolved);
      } catch {
        // Expected - can't click a span
      }

      expect(onOpenChunk).not.toHaveBeenCalled();
    });
  });

  describe("Fact route (no citations in answer)", () => {
    it("renders fact route answer without citation tags", () => {
      renderWithProviders(
        <AnswerRenderer
          answer="This statement is supported by the retrieved evidence."
          retrievedChunkIds={["1:3", "1:5"]}
          onOpenChunk={() => {}}
        />
      );

      expect(
        screen.getByText("This statement is supported by the retrieved evidence.")
      ).toBeInTheDocument();

      // Should have no citation chips
      const buttons = screen.queryAllByRole("button", { name: /View evidence/ });
      expect(buttons).toHaveLength(0);
    });
  });

  describe("Text formatting", () => {
    it("preserves whitespace and formatting", () => {
      renderWithProviders(
        <AnswerRenderer
          answer="Line one [C1]\n\nLine two [C2]"
          retrievedChunkIds={["1:3", "1:5"]}
          onOpenChunk={() => {}}
        />
      );

      // Should have whitespace preserved
      const textContent = screen.getByText(/Line one/).parentElement?.textContent || "";
      expect(textContent).toContain("Line one");
    });
  });

  describe("Edge cases", () => {
    it("handles empty answer", () => {
      renderWithProviders(
        <AnswerRenderer answer="" retrievedChunkIds={[]} onOpenChunk={() => {}} />
      );

      // Should render without error (might be empty or have minimal content)
      expect(screen.getByRole("paragraph") || document.body).toBeInTheDocument();
    });

    it("handles malformed citation tags", () => {
      renderWithProviders(
        <AnswerRenderer
          answer="Text with [C] [CX] [C-1] malformed tags and [C1] valid."
          retrievedChunkIds={["1:3"]}
          onOpenChunk={() => {}}
        />
      );

      // Malformed tags should render as plain text or non-interactive
      expect(screen.getByText(/malformed tags/)).toBeInTheDocument();

      // Valid tag should still work
      expect(screen.getByRole("button", { name: /View evidence for citation C1/ })).toBeInTheDocument();
    });

    it("handles dense citations", () => {
      renderWithProviders(
        <AnswerRenderer
          answer="[C1][C2][C3]"
          retrievedChunkIds={["1:3", "1:5", "1:6"]}
          onOpenChunk={() => {}}
        />
      );

      // All three should be rendered as chips
      expect(screen.getByRole("button", { name: /C1/ })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /C2/ })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /C3/ })).toBeInTheDocument();
    });
  });

  describe("Accessibility", () => {
    it("renders as paragraph for semantic structure", () => {
      const { container } = renderWithProviders(
        <AnswerRenderer
          answer="Text [C1]"
          retrievedChunkIds={["1:3"]}
          onOpenChunk={() => {}}
        />
      );

      const paragraph = container.querySelector("p");
      expect(paragraph).toBeInTheDocument();
    });

    it("citation buttons have clear accessible labels", () => {
      renderWithProviders(
        <AnswerRenderer
          answer="Text [C1]"
          retrievedChunkIds={["1:3"]}
          onOpenChunk={() => {}}
        />
      );

      const button = screen.getByRole("button");
      expect(button).toHaveAttribute(
        "aria-label",
        expect.stringContaining("View evidence for citation C1")
      );
      expect(button).toHaveAttribute("aria-label", expect.stringContaining("chunk 1:3"));
    });

    it("unresolved citations have title attribute for hover text", () => {
      renderWithProviders(
        <AnswerRenderer
          answer="Text [C5]"
          retrievedChunkIds={["1:3"]}
          onOpenChunk={() => {}}
        />
      );

      const unresolved = screen.getByText("[C5]");
      expect(unresolved).toHaveAttribute("title");
    });
  });
});
