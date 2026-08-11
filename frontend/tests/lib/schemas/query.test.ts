import { describe, expect, it } from "vitest";

import { FactCheckedResponseSchema } from "@/lib/schemas/query";

describe("FactCheckedResponseSchema — Zod validation", () => {
  describe("question-route response shape", () => {
    it("parses valid question-route response", () => {
      const data = {
        input_type: "question" as const,
        question: "What is RISC?",
        answer: "RISC uses a reduced instruction set [C1]",
        state: "ok" as const,
        claims: [
          {
            text: "RISC uses a reduced instruction set",
            status: "SUPPORTED" as const,
            citations: ["C1"],
            evidence_score: 0.987,
            chunk_ids: ["1:3"],
            reason: null,
          },
        ],
        retrieved_chunk_ids: ["1:3"],
        latency_ms: {
          retrieval_ms: 100,
          rerank_ms: 50,
          generation_ms: 200,
          verification_ms: 100,
        },
        rejected_claims: [],
        refusals: [],
      };

      const result = FactCheckedResponseSchema.safeParse(data);
      expect(result.success).toBe(true);
      if (result.success) {
        expect(result.data.input_type).toBe("question");
        expect(result.data.latency_ms.generation_ms).toBe(200);
      }
    });
  });

  describe("fact-route response shape", () => {
    it("parses valid fact-route response without generation_ms", () => {
      const data = {
        input_type: "fact" as const,
        question: "RISC has instruction pipelining",
        answer: "This statement is supported by the retrieved evidence",
        state: "ok" as const,
        claims: [
          {
            text: "RISC has instruction pipelining",
            status: "SUPPORTED" as const,
            citations: ["C1"],
            evidence_score: 0.995,
            chunk_ids: ["1:3"],
            reason: null,
          },
        ],
        retrieved_chunk_ids: ["1:3", "1:5"],
        latency_ms: {
          retrieval_ms: 100,
          verification_ms: 50,
          // No generation_ms — this is the key difference
        },
        rejected_claims: [],
        refusals: [],
      };

      const result = FactCheckedResponseSchema.safeParse(data);
      expect(result.success).toBe(true);
      if (result.success) {
        expect(result.data.input_type).toBe("fact");
        expect(result.data.latency_ms.generation_ms).toBeUndefined();
        expect(result.data.latency_ms.retrieval_ms).toBe(100);
        expect(result.data.latency_ms.verification_ms).toBe(50);
      }
    });
  });

  describe("latency_ms as open-ended Record", () => {
    it("accepts unknown keys in latency_ms", () => {
      const data = {
        input_type: "question" as const,
        question: "test",
        answer: "answer",
        state: "ok" as const,
        claims: [],
        retrieved_chunk_ids: [],
        latency_ms: {
          retrieval_ms: 100,
          custom_future_stage_ms: 50, // Unknown key that future backend adds
          another_new_metric: 25,
        },
        rejected_claims: [],
        refusals: [],
      };

      const result = FactCheckedResponseSchema.safeParse(data);
      expect(result.success).toBe(true);
      if (result.success) {
        expect(result.data.latency_ms.custom_future_stage_ms).toBe(50);
        expect(result.data.latency_ms.another_new_metric).toBe(25);
      }
    });

    it("accepts empty latency_ms object", () => {
      const data = {
        input_type: "question" as const,
        question: "test",
        answer: "answer",
        state: "ok" as const,
        claims: [],
        retrieved_chunk_ids: [],
        latency_ms: {}, // Empty is valid
        rejected_claims: [],
        refusals: [],
      };

      const result = FactCheckedResponseSchema.safeParse(data);
      expect(result.success).toBe(true);
    });
  });

  describe("evidence_score handling", () => {
    it("accepts null evidence_score for REFUSAL claims", () => {
      const data = {
        input_type: "question" as const,
        question: "test",
        answer: "answer [C1]",
        state: "ok" as const,
        claims: [
          {
            text: "I cannot answer that",
            status: "REFUSAL" as const,
            citations: ["C1"],
            evidence_score: null, // Null is required for REFUSAL
            chunk_ids: [],
            reason: null,
          },
        ],
        retrieved_chunk_ids: ["1:3"],
        latency_ms: { retrieval_ms: 100 },
        rejected_claims: [],
        refusals: [],
      };

      const result = FactCheckedResponseSchema.safeParse(data);
      expect(result.success).toBe(true);
      if (result.success) {
        expect(result.data.claims[0]?.evidence_score).toBeNull();
      }
    });

    it("accepts number evidence_score for SUPPORTED claims", () => {
      const data = {
        input_type: "question" as const,
        question: "test",
        answer: "answer [C1]",
        state: "ok" as const,
        claims: [
          {
            text: "test claim",
            status: "SUPPORTED" as const,
            citations: ["C1"],
            evidence_score: 0.987, // Must be a number
            chunk_ids: ["1:3"],
            reason: null,
          },
        ],
        retrieved_chunk_ids: ["1:3"],
        latency_ms: { retrieval_ms: 100 },
        rejected_claims: [],
        refusals: [],
      };

      const result = FactCheckedResponseSchema.safeParse(data);
      expect(result.success).toBe(true);
      if (result.success) {
        expect(typeof result.data.claims[0]?.evidence_score).toBe("number");
      }
    });
  });

  describe("all claim statuses", () => {
    const baseData = {
      input_type: "question" as const,
      question: "test",
      answer: "answer",
      state: "ok" as const,
      retrieved_chunk_ids: [],
      latency_ms: {},
      rejected_claims: [],
      refusals: [],
    };

    it("accepts SUPPORTED status", () => {
      const data = {
        ...baseData,
        claims: [
          {
            text: "test",
            status: "SUPPORTED" as const,
            citations: [],
            evidence_score: 0.9,
            chunk_ids: [],
            reason: null,
          },
        ],
      };

      const result = FactCheckedResponseSchema.safeParse(data);
      expect(result.success).toBe(true);
    });

    it("accepts UNSUPPORTED status", () => {
      const data = {
        ...baseData,
        claims: [
          {
            text: "test",
            status: "UNSUPPORTED" as const,
            citations: [],
            evidence_score: 0.3,
            chunk_ids: [],
            reason: "Score below 0.55 threshold",
          },
        ],
      };

      const result = FactCheckedResponseSchema.safeParse(data);
      expect(result.success).toBe(true);
    });

    it("accepts CONTRADICTED status", () => {
      const data = {
        ...baseData,
        claims: [
          {
            text: "test",
            status: "CONTRADICTED" as const,
            citations: [],
            evidence_score: 0.95,
            chunk_ids: [],
            reason: "Contradicted by [C2]",
          },
        ],
      };

      const result = FactCheckedResponseSchema.safeParse(data);
      expect(result.success).toBe(true);
    });

    it("accepts REFUSAL status", () => {
      const data = {
        ...baseData,
        claims: [
          {
            text: "I cannot answer",
            status: "REFUSAL" as const,
            citations: [],
            evidence_score: null,
            chunk_ids: [],
            reason: null,
          },
        ],
      };

      const result = FactCheckedResponseSchema.safeParse(data);
      expect(result.success).toBe(true);
    });

    it("rejects unknown status value", () => {
      const data = {
        ...baseData,
        claims: [
          {
            text: "test",
            status: "UNKNOWN",
            citations: [],
            evidence_score: 0.5,
            chunk_ids: [],
            reason: null,
          },
        ],
      };

      const result = FactCheckedResponseSchema.safeParse(data);
      expect(result.success).toBe(false);
    });
  });

  describe("all query states", () => {
    const baseData = {
      input_type: "question" as const,
      question: "test",
      answer: "answer",
      claims: [],
      retrieved_chunk_ids: [],
      latency_ms: {},
      rejected_claims: [],
      refusals: [],
    };

    it("accepts ok state", () => {
      const data = { ...baseData, state: "ok" as const };
      const result = FactCheckedResponseSchema.safeParse(data);
      expect(result.success).toBe(true);
    });

    it("accepts insufficient_evidence state", () => {
      const data = { ...baseData, state: "insufficient_evidence" as const };
      const result = FactCheckedResponseSchema.safeParse(data);
      expect(result.success).toBe(true);
    });

    it("accepts contradicted state (fact route only)", () => {
      const data = { ...baseData, state: "contradicted" as const };
      const result = FactCheckedResponseSchema.safeParse(data);
      expect(result.success).toBe(true);
    });

    it("rejects unknown state value", () => {
      const data = { ...baseData, state: "unknown" };
      const result = FactCheckedResponseSchema.safeParse(data);
      expect(result.success).toBe(false);
    });
  });

  describe("malformed payloads", () => {
    it("rejects response missing required field (question)", () => {
      const data = {
        input_type: "question" as const,
        answer: "answer",
        state: "ok" as const,
        claims: [],
        retrieved_chunk_ids: [],
        latency_ms: {},
        rejected_claims: [],
        refusals: [],
        // Missing: question
      };

      const result = FactCheckedResponseSchema.safeParse(data);
      expect(result.success).toBe(false);
    });

    it("rejects response with wrong type for field (claims not array)", () => {
      const data = {
        input_type: "question" as const,
        question: "test",
        answer: "answer",
        state: "ok" as const,
        claims: "not an array", // Wrong type
        retrieved_chunk_ids: [],
        latency_ms: {},
        rejected_claims: [],
        refusals: [],
      };

      const result = FactCheckedResponseSchema.safeParse(data);
      expect(result.success).toBe(false);
    });

    it("provides useful error message on validation failure", () => {
      const data = {
        input_type: "question" as const,
        question: "test",
        answer: "answer",
        state: "ok" as const,
        claims: [
          {
            text: "test",
            status: "INVALID",
            citations: [],
            evidence_score: 0.5,
            chunk_ids: [],
            reason: null,
          },
        ],
        retrieved_chunk_ids: [],
        latency_ms: {},
        rejected_claims: [],
        refusals: [],
      };

      const result = FactCheckedResponseSchema.safeParse(data);
      expect(result.success).toBe(false);
      if (!result.success) {
        expect(result.error.message).toBeTruthy();
      }
    });
  });

  describe("rejected_claims and refusals projections", () => {
    it("accepts empty arrays for both", () => {
      const data = {
        input_type: "question" as const,
        question: "test",
        answer: "answer",
        state: "ok" as const,
        claims: [],
        retrieved_chunk_ids: [],
        latency_ms: {},
        rejected_claims: [],
        refusals: [],
      };

      const result = FactCheckedResponseSchema.safeParse(data);
      expect(result.success).toBe(true);
    });

    it("accepts populated rejected_claims", () => {
      const data = {
        input_type: "question" as const,
        question: "test",
        answer: "answer [C1]",
        state: "ok" as const,
        claims: [
          {
            text: "claim",
            status: "UNSUPPORTED" as const,
            citations: ["C1"],
            evidence_score: 0.3,
            chunk_ids: ["1:3"],
            reason: "Low score",
          },
        ],
        retrieved_chunk_ids: ["1:3"],
        latency_ms: {},
        rejected_claims: [
          {
            text: "claim",
            status: "UNSUPPORTED" as const,
            citations: ["C1"],
            evidence_score: 0.3,
            chunk_ids: ["1:3"],
            reason: "Low score",
          },
        ],
        refusals: [],
      };

      const result = FactCheckedResponseSchema.safeParse(data);
      expect(result.success).toBe(true);
      if (result.success) {
        expect(result.data.rejected_claims).toHaveLength(1);
      }
    });

    it("accepts populated refusals", () => {
      const data = {
        input_type: "question" as const,
        question: "test",
        answer: "answer [C1]",
        state: "ok" as const,
        claims: [
          {
            text: "I cannot answer",
            status: "REFUSAL" as const,
            citations: ["C1"],
            evidence_score: null,
            chunk_ids: [],
            reason: null,
          },
        ],
        retrieved_chunk_ids: ["1:3"],
        latency_ms: {},
        rejected_claims: [],
        refusals: [
          {
            text: "I cannot answer",
            status: "REFUSAL" as const,
            citations: ["C1"],
            evidence_score: null,
            chunk_ids: [],
            reason: null,
          },
        ],
      };

      const result = FactCheckedResponseSchema.safeParse(data);
      expect(result.success).toBe(true);
      if (result.success) {
        expect(result.data.refusals).toHaveLength(1);
      }
    });
  });
});
