import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  HISTORY_LIMIT,
  addHistoryEntry,
  clearHistory,
  readHistory,
  removeHistoryEntry,
  type HistoryEntry,
  type NewHistoryEntry,
} from "@/lib/storage/history";

/**
 * Tests for lib/storage/history.ts — pure localStorage-backed query history.
 * All tests run in jsdom with a real localStorage implementation.
 *
 * Key contracts:
 * - readHistory() never throws, degrades on corruption
 * - addHistoryEntry() generates id/ranAt internally
 * - Eviction at HISTORY_LIMIT (50): adding a 51st drops the oldest
 * - SSR-safe: guards typeof window === "undefined"
 * - removeHistoryEntry / clearHistory round-trip
 */

describe("history storage (lib/storage/history.ts)", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  describe("readHistory() — never throws, degrades gracefully", () => {
    it("returns empty array when localStorage is empty", () => {
      expect(readHistory()).toEqual([]);
    });

    it("returns empty array when key is absent", () => {
      localStorage.setItem("other:key", "some value");
      expect(readHistory()).toEqual([]);
    });

    it("returns empty array on invalid JSON", () => {
      localStorage.setItem("knowrag:history", "not valid json {[");
      expect(readHistory()).toEqual([]);
    });

    it("returns empty array when stored value is null", () => {
      localStorage.setItem("knowrag:history", "null");
      expect(readHistory()).toEqual([]);
    });

    it("returns empty array when stored value is not an array", () => {
      localStorage.setItem("knowrag:history", '{"entries": []}');
      expect(readHistory()).toEqual([]);
    });

    it("filters out malformed entries and keeps valid ones", () => {
      const validEntry: HistoryEntry = {
        id: "test-id-1",
        question: "What is X?",
        inputType: "question",
        state: "ok",
        ranAt: "2026-08-11T10:00:00Z",
      };
      const malformed: unknown[] = [
        validEntry,
        { id: "test-id-2", question: "What is Y?" }, // missing fields
        {
          id: "test-id-3",
          question: "What is Z?",
          inputType: "invalid", // invalid inputType
          state: "ok",
          ranAt: "2026-08-11T10:01:00Z",
        },
      ];
      localStorage.setItem("knowrag:history", JSON.stringify(malformed));

      const result = readHistory();
      expect(result).toHaveLength(1);
      expect(result[0]).toEqual(validEntry);
    });

    it("handles array with only invalid entries", () => {
      const malformed = [
        { question: "No id" },
        { id: "id", question: "No state" },
        null,
        undefined,
        "string",
      ];
      localStorage.setItem("knowrag:history", JSON.stringify(malformed));
      expect(readHistory()).toEqual([]);
    });

    it("does not throw on storage access errors (private browsing, disabled storage)", () => {
      // Mock localStorage.getItem to throw
      const getItemSpy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
        throw new Error("Storage access denied");
      });

      expect(() => readHistory()).not.toThrow();
      expect(readHistory()).toEqual([]);

      getItemSpy.mockRestore();
    });
  });

  describe("addHistoryEntry() — generates id and ranAt", () => {
    it("adds a new entry with generated id and ranAt", () => {
      const newEntry: NewHistoryEntry = {
        question: "What is RISC?",
        inputType: "question",
        state: "ok",
      };

      const added = addHistoryEntry(newEntry);

      expect(added.id).toBeDefined();
      expect(typeof added.id).toBe("string");
      expect(added.id.length).toBeGreaterThan(0);
      expect(added.ranAt).toBeDefined();
      expect(typeof added.ranAt).toBe("string");
      // ISO-8601 timestamp
      expect(new Date(added.ranAt)).toBeInstanceOf(Date);

      const read = readHistory();
      expect(read).toHaveLength(1);
      expect(read[0]).toEqual(added);
    });

    it("stores entries newest-first (index 0 is most recent)", () => {
      const entry1 = addHistoryEntry({
        question: "First question",
        inputType: "question",
        state: "ok",
      });

      const entry2 = addHistoryEntry({
        question: "Second question",
        inputType: "question",
        state: "ok",
      });

      const history = readHistory();
      expect(history).toHaveLength(2);
      expect(history[0]!.id).toBe(entry2.id);
      expect(history[1]!.id).toBe(entry1.id);
    });

    it("generates distinct ids for entries added in the same tick", () => {
      const ids = new Set<string>();

      for (let i = 0; i < 10; i++) {
        const added = addHistoryEntry({
          question: `Question ${i}`,
          inputType: "question",
          state: "ok",
        });
        ids.add(added.id);
      }

      expect(ids.size).toBe(10);
    });

    it("persists entries to localStorage", () => {
      addHistoryEntry({
        question: "Test",
        inputType: "question",
        state: "ok",
      });

      const stored = localStorage.getItem("knowrag:history");
      expect(stored).toBeDefined();
      expect(stored).not.toBe(null);

      const parsed = JSON.parse(stored!);
      expect(Array.isArray(parsed)).toBe(true);
      expect(parsed).toHaveLength(1);
    });

    it("does not throw on write failures (quota exceeded, storage disabled)", () => {
      const setItemSpy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
        throw new Error("QuotaExceededError");
      });

      expect(() => {
        addHistoryEntry({
          question: "Test",
          inputType: "question",
          state: "ok",
        });
      }).not.toThrow();

      setItemSpy.mockRestore();
    });
  });

  describe("HISTORY_LIMIT (50) — eviction on overflow", () => {
    it("exports HISTORY_LIMIT constant as 50", () => {
      expect(HISTORY_LIMIT).toBe(50);
    });

    it("keeps exactly 50 entries after reaching the limit", () => {
      // Add 51 entries
      for (let i = 0; i < 51; i++) {
        addHistoryEntry({
          question: `Question ${i}`,
          inputType: "question",
          state: "ok",
        });
      }

      const history = readHistory();
      expect(history).toHaveLength(50);
    });

    it("drops the oldest entries (tail of array) when exceeding limit", () => {
      const ids: string[] = [];

      // Add 52 entries
      for (let i = 0; i < 52; i++) {
        const entry = addHistoryEntry({
          question: `Question ${i}`,
          inputType: "question",
          state: "ok",
        });
        ids.push(entry.id);
      }

      const history = readHistory();

      // Should have exactly 50 entries
      expect(history).toHaveLength(50);

      // The first entry (oldest) should be gone
      expect(history.map((e) => e.id)).not.toContain(ids[0]);

      // The second entry should also be gone (52 entries -> keep 50)
      expect(history.map((e) => e.id)).not.toContain(ids[1]);

      // The 51st and 52nd entries should be present (most recent 50)
      const historyIds = history.map((e) => e.id);
      expect(historyIds).toContain(ids[2]);
      expect(historyIds).toContain(ids[51]);
    });

    it("maintains newest-first order after eviction", () => {
      for (let i = 0; i < HISTORY_LIMIT + 5; i++) {
        addHistoryEntry({
          question: `Question ${i}`,
          inputType: "question",
          state: "ok",
        });
      }

      const history = readHistory();

      // Check that timestamps are in descending order (newest first)
      for (let i = 0; i < history.length - 1; i++) {
        const current = new Date(history[i]!.ranAt).getTime();
        const next = new Date(history[i + 1]!.ranAt).getTime();
        expect(current).toBeGreaterThanOrEqual(next);
      }
    });
  });

  describe("removeHistoryEntry(id) — delete by id", () => {
    it("removes a single entry by id and returns updated list", () => {
      const entry1 = addHistoryEntry({
        question: "Keep this",
        inputType: "question",
        state: "ok",
      });

      const entry2 = addHistoryEntry({
        question: "Remove this",
        inputType: "question",
        state: "ok",
      });

      const updated = removeHistoryEntry(entry2.id);

      expect(updated).toHaveLength(1);
      expect(updated[0]!.id).toBe(entry1.id);

      // Persisted to localStorage
      expect(readHistory()).toEqual(updated);
    });

    it("removes an id that doesn't exist without throwing", () => {
      addHistoryEntry({
        question: "Keep this",
        inputType: "question",
        state: "ok",
      });

      expect(() => {
        removeHistoryEntry("nonexistent-id");
      }).not.toThrow();

      const history = readHistory();
      expect(history).toHaveLength(1);
    });

    it("returns empty array after removing the only entry", () => {
      const entry = addHistoryEntry({
        question: "Only entry",
        inputType: "question",
        state: "ok",
      });

      const updated = removeHistoryEntry(entry.id);
      expect(updated).toEqual([]);
      expect(readHistory()).toEqual([]);
    });

    it("round-trips: add, remove, verify readHistory", () => {
      const entry1 = addHistoryEntry({
        question: "Q1",
        inputType: "question",
        state: "ok",
      });

      const entry2 = addHistoryEntry({
        question: "Q2",
        inputType: "fact",
        state: "insufficient_evidence",
      });

      removeHistoryEntry(entry2.id);

      const history = readHistory();
      expect(history).toHaveLength(1);
      expect(history[0]).toEqual(entry1);
    });
  });

  describe("clearHistory() — wipe all", () => {
    it("removes all entries", () => {
      addHistoryEntry({
        question: "Q1",
        inputType: "question",
        state: "ok",
      });

      addHistoryEntry({
        question: "Q2",
        inputType: "question",
        state: "ok",
      });

      clearHistory();
      expect(readHistory()).toEqual([]);
    });

    it("does not throw when called on empty storage", () => {
      expect(() => {
        clearHistory();
      }).not.toThrow();
    });

    it("removes the localStorage key entirely", () => {
      addHistoryEntry({
        question: "Test",
        inputType: "question",
        state: "ok",
      });

      clearHistory();
      expect(localStorage.getItem("knowrag:history")).toBeNull();
    });

    it("does not throw on storage access errors", () => {
      const removeItemSpy = vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
        throw new Error("Storage access denied");
      });

      expect(() => {
        clearHistory();
      }).not.toThrow();

      removeItemSpy.mockRestore();
    });

    it("round-trips: add, clear, add again", () => {
      const entry1 = addHistoryEntry({
        question: "Q1",
        inputType: "question",
        state: "ok",
      });

      clearHistory();
      expect(readHistory()).toEqual([]);

      const entry2 = addHistoryEntry({
        question: "Q2",
        inputType: "question",
        state: "ok",
      });

      const history = readHistory();
      expect(history).toHaveLength(1);
      expect(history[0]!.id).toBe(entry2.id);
      expect(history[0]!.id).not.toBe(entry1.id);
    });
  });

  describe("SSR safety — guards typeof window === 'undefined'", () => {
    it("readHistory returns [] on server (no window)", () => {
      const originalWindow = global.window;
      // @ts-expect-error - deliberately undefining window for SSR test
      delete global.window;

      const result = readHistory();
      expect(result).toEqual([]);

      global.window = originalWindow;
    });

    it("addHistoryEntry is a no-op on server (no window)", () => {
      const originalWindow = global.window;
      // @ts-expect-error - deliberately undefining window for SSR test
      delete global.window;

      // Should not throw
      const result = addHistoryEntry({
        question: "Test",
        inputType: "question",
        state: "ok",
      });

      // Entry should have id and ranAt but not be persisted
      expect(result.id).toBeDefined();
      expect(result.ranAt).toBeDefined();

      global.window = originalWindow;
    });

    it("clearHistory is a no-op on server (no window)", () => {
      const originalWindow = global.window;
      // @ts-expect-error - deliberately undefining window for SSR test
      delete global.window;

      // Should not throw
      expect(() => {
        clearHistory();
      }).not.toThrow();

      global.window = originalWindow;
    });

    it("removeHistoryEntry is a no-op on server (no window)", () => {
      const originalWindow = global.window;
      // @ts-expect-error - deliberately undefining window for SSR test
      delete global.window;

      // Should not throw
      expect(() => {
        removeHistoryEntry("any-id");
      }).not.toThrow();

      global.window = originalWindow;
    });
  });

  describe("HistoryEntry type validation", () => {
    it("stored entries match HistoryEntry shape: all required fields", () => {
      const entry = addHistoryEntry({
        question: "Test question",
        inputType: "fact",
        state: "contradicted",
      });

      expect(entry).toHaveProperty("id");
      expect(entry).toHaveProperty("question");
      expect(entry).toHaveProperty("inputType");
      expect(entry).toHaveProperty("state");
      expect(entry).toHaveProperty("ranAt");

      expect(entry.question).toBe("Test question");
      expect(entry.inputType).toBe("fact");
      expect(entry.state).toBe("contradicted");
    });

    it("accepts valid inputType values: question and fact", () => {
      const q = addHistoryEntry({
        question: "Q",
        inputType: "question",
        state: "ok",
      });

      const f = addHistoryEntry({
        question: "F",
        inputType: "fact",
        state: "ok",
      });

      const history = readHistory();
      expect(history.map((e) => e.inputType)).toContain("question");
      expect(history.map((e) => e.inputType)).toContain("fact");
    });

    it("accepts valid state values: ok, insufficient_evidence, contradicted", () => {
      addHistoryEntry({
        question: "Q1",
        inputType: "question",
        state: "ok",
      });

      addHistoryEntry({
        question: "Q2",
        inputType: "question",
        state: "insufficient_evidence",
      });

      addHistoryEntry({
        question: "Q3",
        inputType: "question",
        state: "contradicted",
      });

      const history = readHistory();
      const states = history.map((e) => e.state);
      expect(states).toContain("ok");
      expect(states).toContain("insufficient_evidence");
      expect(states).toContain("contradicted");
    });
  });
});
