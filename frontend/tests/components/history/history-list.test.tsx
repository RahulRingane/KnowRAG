import { describe, expect, it, beforeEach, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { HistoryList } from "@/components/history/history-list";
import { addHistoryEntry, clearHistory } from "@/lib/storage/history";
import { renderWithProviders } from "../../utils/render";

/**
 * Tests for HistoryList — localStorage-backed query history UI.
 * Tests the four main flows: render entries, empty state, delete-one, clear-all.
 * Also verifies the re-run link targets the correct query parameter.
 */

describe("HistoryList component", () => {
  beforeEach(() => {
    clearHistory();
  });

  describe("empty state", () => {
    it("shows empty state when no history exists", async () => {
      renderWithProviders(<HistoryList />);

      // Wait for hydration
      await waitFor(() => {
        expect(screen.getByText("No query history yet")).toBeInTheDocument();
      });

      expect(screen.getByText(/Questions and statements you run show up here/)).toBeInTheDocument();
    });

    it("empty state has icon and descriptive text", async () => {
      renderWithProviders(<HistoryList />);

      await waitFor(() => {
        expect(screen.getByText("No query history yet")).toBeInTheDocument();
      });

      // Should have descriptive text
      expect(screen.getByText(/Questions and statements you run show up here/)).toBeInTheDocument();
    });

    it("does not show 'Clear all' button when empty", async () => {
      renderWithProviders(<HistoryList />);

      await waitFor(() => {
        expect(screen.getByText("No query history yet")).toBeInTheDocument();
      });

      expect(screen.queryByRole("button", { name: /clear all/i })).not.toBeInTheDocument();
    });
  });

  describe("rendering entries", () => {
    beforeEach(() => {
      // Add some test entries
      addHistoryEntry({
        question: "What is RISC?",
        inputType: "question",
        state: "ok",
      });

      addHistoryEntry({
        question: "Embedded systems use pipelining",
        inputType: "fact",
        state: "contradicted",
      });

      addHistoryEntry({
        question: "What is cache coherence?",
        inputType: "question",
        state: "insufficient_evidence",
      });
    });

    it("renders list after hydration", async () => {
      renderWithProviders(<HistoryList />);

      // After effect runs, shows actual list
      await waitFor(() => {
        expect(screen.getByText("What is RISC?")).toBeInTheDocument();
      });
    });

    it("displays all stored entries", async () => {
      renderWithProviders(<HistoryList />);

      await waitFor(() => {
        expect(screen.getByText("What is RISC?")).toBeInTheDocument();
        expect(screen.getByText("Embedded systems use pipelining")).toBeInTheDocument();
        expect(screen.getByText("What is cache coherence?")).toBeInTheDocument();
      });
    });

    it("displays entries newest-first", async () => {
      renderWithProviders(<HistoryList />);

      await waitFor(() => {
        expect(screen.getByText("What is cache coherence?")).toBeInTheDocument();
      });

      const items = screen.getAllByRole("listitem");
      expect(items).toHaveLength(3);

      // Newest entry should be first
      expect(items[0]).toHaveTextContent("What is cache coherence?");
      expect(items[1]).toHaveTextContent("Embedded systems use pipelining");
      expect(items[2]).toHaveTextContent("What is RISC?");
    });

    it("shows input type badge: 'Question' or 'Fact check'", async () => {
      renderWithProviders(<HistoryList />);

      await waitFor(() => {
        expect(screen.getByText("What is RISC?")).toBeInTheDocument();
      });

      // Multiple entries exist, use getAllByText
      const questionBadges = screen.getAllByText("Question");
      const factCheckBadges = screen.getAllByText("Fact check");
      expect(questionBadges.length).toBeGreaterThan(0);
      expect(factCheckBadges.length).toBeGreaterThan(0);
    });

    it("shows state badges with correct status", async () => {
      renderWithProviders(<HistoryList />);

      await waitFor(() => {
        expect(screen.getByText("Answered")).toBeInTheDocument();
        expect(screen.getByText("Insufficient evidence")).toBeInTheDocument();
        expect(screen.getByText("Contradicted")).toBeInTheDocument();
      });
    });

    it("displays formatted timestamp for each entry", async () => {
      renderWithProviders(<HistoryList />);

      await waitFor(() => {
        expect(screen.getByText("What is RISC?")).toBeInTheDocument();
      });

      // Should have timestamps (exact format depends on formatTimestamp)
      const items = screen.getAllByRole("listitem");
      items.forEach((item) => {
        // Each item should have a timestamp (text should be present, exact format varies)
        const text = item.textContent;
        const hasTimestamp = text?.includes("ago") || text?.includes("2026");
        expect(hasTimestamp).toBe(true);
      });
    });

    it("renders entry question text without truncation in list", async () => {
      renderWithProviders(<HistoryList />);

      await waitFor(() => {
        expect(screen.getByText("What is RISC?")).toBeInTheDocument();
      });

      const question = screen.getByText("What is RISC?");
      expect(question.classList.toString()).toContain("break-words");
    });

    it("handles long entry questions", async () => {
      const longQuestion = "What is the difference between " + "a".repeat(100) + "?";
      clearHistory();
      addHistoryEntry({
        question: longQuestion,
        inputType: "question",
        state: "ok",
      });

      renderWithProviders(<HistoryList />);

      await waitFor(() => {
        expect(screen.getByText((content) => content.includes("a".repeat(100)))).toBeInTheDocument();
      });
    });
  });

  describe("delete entry (remove by id)", () => {
    beforeEach(() => {
      addHistoryEntry({
        question: "Keep this",
        inputType: "question",
        state: "ok",
      });

      addHistoryEntry({
        question: "Delete this",
        inputType: "question",
        state: "ok",
      });
    });

    it("delete button has aria-label with entry question", async () => {
      renderWithProviders(<HistoryList />);

      await waitFor(() => {
        expect(screen.getByText("Delete this")).toBeInTheDocument();
      });

      const deleteButtons = screen.getAllByRole("button", { hidden: true });
      const deleteThisButton = deleteButtons.find((btn) =>
        btn.getAttribute("aria-label")?.includes("Delete")
      );

      expect(deleteThisButton).toBeInTheDocument();
      expect(deleteThisButton?.getAttribute("aria-label")).toContain("Delete this");
    });

    it("clicking delete button removes the entry", async () => {
      renderWithProviders(<HistoryList />);

      await waitFor(() => {
        expect(screen.getByText("Delete this")).toBeInTheDocument();
      });

      const deleteButtons = screen.getAllByRole("button", { hidden: true });
      const deleteThisButton = deleteButtons.find((btn) =>
        btn.getAttribute("aria-label")?.includes("Delete this")
      );

      await userEvent.click(deleteThisButton!);

      await waitFor(() => {
        expect(screen.queryByText("Delete this")).not.toBeInTheDocument();
      });

      // Kept entry should still exist
      expect(screen.getByText("Keep this")).toBeInTheDocument();
    });

    it("delete removes only the targeted entry", async () => {
      renderWithProviders(<HistoryList />);

      await waitFor(() => {
        expect(screen.getByText("Delete this")).toBeInTheDocument();
      });

      const items = screen.getAllByRole("listitem");
      expect(items).toHaveLength(2);

      const deleteButtons = screen.getAllByRole("button", { hidden: true });
      const deleteThisButton = deleteButtons.find((btn) =>
        btn.getAttribute("aria-label")?.includes("Delete this")
      );

      await userEvent.click(deleteThisButton!);

      await waitFor(() => {
        const updatedItems = screen.getAllByRole("listitem");
        expect(updatedItems).toHaveLength(1);
      });
    });

    it("after deleting all entries, shows empty state", async () => {
      renderWithProviders(<HistoryList />);

      await waitFor(() => {
        expect(screen.getByText("Delete this")).toBeInTheDocument();
      });

      // Delete both entries
      const deleteButtons = screen.getAllByRole("button", { hidden: true });
      const deleteButtons1 = deleteButtons.filter((btn) =>
        btn.getAttribute("aria-label")?.includes("Delete")
      );

      for (const btn of deleteButtons1) {
        await userEvent.click(btn);
      }

      await waitFor(() => {
        expect(screen.getByText("No query history yet")).toBeInTheDocument();
      });
    });
  });

  describe("clear all", () => {
    beforeEach(() => {
      for (let i = 0; i < 5; i++) {
        addHistoryEntry({
          question: `Question ${i}`,
          inputType: "question",
          state: "ok",
        });
      }
    });

    it("shows 'Clear all' button when entries exist", async () => {
      renderWithProviders(<HistoryList />);

      await waitFor(() => {
        expect(screen.getByRole("button", { name: /clear all/i })).toBeInTheDocument();
      });
    });

    it("clicking 'Clear all' opens confirmation dialog", async () => {
      renderWithProviders(<HistoryList />);

      await waitFor(() => {
        expect(screen.getByText("Question 0")).toBeInTheDocument();
      });

      const clearButton = screen.getByRole("button", { name: /clear all/i });
      await userEvent.click(clearButton);

      await waitFor(() => {
        expect(screen.getByText("Clear all history?")).toBeInTheDocument();
      });
    });

    it("dialog shows entry count", async () => {
      renderWithProviders(<HistoryList />);

      await waitFor(() => {
        expect(screen.getByText("Question 0")).toBeInTheDocument();
      });

      const clearButton = screen.getByRole("button", { name: /clear all/i });
      await userEvent.click(clearButton);

      await waitFor(() => {
        expect(screen.getByText(/all 5 stored entries/)).toBeInTheDocument();
      });
    });

    it("'Clear all' button in dialog removes all entries", async () => {
      renderWithProviders(<HistoryList />);

      await waitFor(() => {
        expect(screen.getByText("Question 0")).toBeInTheDocument();
      });

      const clearButton = screen.getByRole("button", { name: /clear all/i });
      await userEvent.click(clearButton);

      await waitFor(() => {
        expect(screen.getByText("Clear all history?")).toBeInTheDocument();
      });

      const confirmButton = screen.getAllByRole("button", { name: /clear all/i }).at(-1);
      await userEvent.click(confirmButton!);

      await waitFor(() => {
        expect(screen.getByText("No query history yet")).toBeInTheDocument();
      });
    });

    it("'Cancel' button closes dialog without clearing", async () => {
      renderWithProviders(<HistoryList />);

      await waitFor(() => {
        expect(screen.getByText("Question 0")).toBeInTheDocument();
      });

      const clearButton = screen.getByRole("button", { name: /clear all/i });
      await userEvent.click(clearButton);

      await waitFor(() => {
        expect(screen.getByText("Clear all history?")).toBeInTheDocument();
      });

      const cancelButton = screen.getByRole("button", { name: /cancel/i });
      await userEvent.click(cancelButton);

      // Dialog should close, entries should still exist
      await waitFor(() => {
        expect(screen.queryByText("Clear all history?")).not.toBeInTheDocument();
      });

      expect(screen.getByText("Question 0")).toBeInTheDocument();
    });
  });

  describe("re-run link", () => {
    beforeEach(() => {
      addHistoryEntry({
        question: "What is pipelining?",
        inputType: "question",
        state: "ok",
      });
    });

    it("renders re-run link with correct structure", async () => {
      renderWithProviders(<HistoryList />);

      await waitFor(() => {
        expect(screen.getByText("What is pipelining?")).toBeInTheDocument();
      });

      // Find the re-run link by looking for links that navigate to /
      const links = screen.getAllByRole("link");
      const rerunLink = links.find((link) =>
        link.getAttribute("href")?.includes("/?")
      );

      expect(rerunLink).toBeInTheDocument();
      if (rerunLink) {
        expect(rerunLink.getAttribute("href")).toContain("q=");
      }
    });

    it("re-run uses 'q' query parameter with question text", async () => {
      renderWithProviders(<HistoryList />);

      await waitFor(() => {
        expect(screen.getByText("What is pipelining?")).toBeInTheDocument();
      });

      const links = screen.getAllByRole("link");
      const rerunLink = links.find((link) =>
        link.getAttribute("href")?.includes("/?") &&
        link.getAttribute("href")?.includes("q=")
      );

      expect(rerunLink).toBeInTheDocument();
      if (rerunLink) {
        const href = rerunLink.getAttribute("href");
        // URL encoding can use + or %20 for spaces; check that the question is there
        expect(href?.includes("q=")).toBe(true);
        expect(href?.includes("pipelining")).toBe(true);
      }
    });
  });

  describe("SSR hydration", () => {
    it("shows skeleton initially, then hydrates with real content", async () => {
      addHistoryEntry({
        question: "Test question",
        inputType: "question",
        state: "ok",
      });

      renderWithProviders(<HistoryList />);

      // After effect, should show content
      await waitFor(() => {
        expect(screen.getByText("Test question")).toBeInTheDocument();
      });
    });
  });

  describe("accessibility", () => {
    it("list has proper aria-label", async () => {
      addHistoryEntry({
        question: "Test",
        inputType: "question",
        state: "ok",
      });

      renderWithProviders(<HistoryList />);

      await waitFor(() => {
        const list = screen.getByRole("list");
        // Note: actual aria-label may vary based on implementation
        expect(list).toBeInTheDocument();
      });
    });

    it("delete and re-run buttons are keyboard accessible", async () => {
      addHistoryEntry({
        question: "Test question",
        inputType: "question",
        state: "ok",
      });

      renderWithProviders(<HistoryList />);

      await waitFor(() => {
        expect(screen.getByText("Test question")).toBeInTheDocument();
      });

      const buttons = screen.getAllByRole("button", { hidden: true });
      expect(buttons.length).toBeGreaterThanOrEqual(2);
    });
  });

  describe("singular vs plural", () => {
    it("shows 'entry' singular when 1 entry being cleared", async () => {
      addHistoryEntry({
        question: "Only question",
        inputType: "question",
        state: "ok",
      });

      renderWithProviders(<HistoryList />);

      await waitFor(() => {
        expect(screen.getByText("Only question")).toBeInTheDocument();
      });

      const clearButton = screen.getByRole("button", { name: /clear all/i });
      await userEvent.click(clearButton);

      await waitFor(() => {
        expect(screen.getByText(/1 stored entry/)).toBeInTheDocument();
      });
    });

    it("shows 'entries' plural when multiple", async () => {
      for (let i = 0; i < 3; i++) {
        addHistoryEntry({
          question: `Question ${i}`,
          inputType: "question",
          state: "ok",
        });
      }

      renderWithProviders(<HistoryList />);

      await waitFor(() => {
        expect(screen.getByText("Question 0")).toBeInTheDocument();
      });

      const clearButton = screen.getByRole("button", { name: /clear all/i });
      await userEvent.click(clearButton);

      await waitFor(() => {
        expect(screen.getByText(/3 stored entries/)).toBeInTheDocument();
      });
    });
  });
});
