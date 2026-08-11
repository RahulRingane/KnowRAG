import type { FactCheckedResponse, ClaimVerdict } from "@/types/api";

/**
 * Test fixtures for verification result components.
 * Each fixture represents a realistic API response matching documented backend behavior.
 */

/** A claim that appears in both answer and rejected_claims (UNSUPPORTED on question route). */
const unsupportedClaim: ClaimVerdict = {
  text: "This is a claim that could not be fully confirmed",
  status: "UNSUPPORTED",
  citations: ["C1", "C2"],
  evidence_score: 0.45,
  chunk_ids: ["1:3", "1:5"],
  reason: "No single chunk entails this paraphrase verbatim",
};

/** A claim present in all three lists - claims, rejected_claims, and explicitly in answer. */
const contradictedClaim: ClaimVerdict = {
  text: "RISC has a complex instruction set",
  status: "CONTRADICTED",
  citations: ["C1"],
  evidence_score: 0.12,
  chunk_ids: ["1:3"],
  reason: "The evidence contradicts this statement",
};

/** A supported claim. */
const supportedClaim: ClaimVerdict = {
  text: "RISC uses a reduced instruction set",
  status: "SUPPORTED",
  citations: ["C1"],
  evidence_score: 0.987,
  chunk_ids: ["1:3"],
  reason: null,
};

/** A refusal claim (never scored). */
const refusalClaim: ClaimVerdict = {
  text: "I cannot provide information about this topic",
  status: "REFUSAL",
  citations: [],
  evidence_score: null,
  chunk_ids: [],
  reason: null,
};

/**
 * Question route response - normal successful query.
 * answer contains SUPPORTED and UNSUPPORTED claims.
 */
export const questionRouteOkResponse: FactCheckedResponse = {
  input_type: "question",
  question: "What is RISC?",
  answer:
    "RISC uses a reduced instruction set [C1]. This is a claim that could not be fully confirmed [C1][C2].",
  state: "ok",
  claims: [supportedClaim, unsupportedClaim],
  retrieved_chunk_ids: ["1:3", "1:5", "1:6"],
  latency_ms: {
    retrieval_ms: 100,
    rerank_ms: 50,
    generation_ms: 200,
    verification_ms: 100,
  },
  rejected_claims: [unsupportedClaim],
  refusals: [],
};

/**
 * Question route with insufficient evidence.
 * state: "insufficient_evidence" is a normal 200, not an error.
 */
export const questionRouteInsufficientResponse: FactCheckedResponse = {
  input_type: "question",
  question: "What is quantum entanglement in this specific corpus?",
  answer: "The evidence in this corpus does not contain sufficient information to answer this question.",
  state: "insufficient_evidence",
  claims: [],
  retrieved_chunk_ids: ["1:1", "1:2"],
  latency_ms: {
    retrieval_ms: 100,
    rerank_ms: 50,
    verification_ms: 100,
  },
  rejected_claims: [],
  refusals: [],
};

/**
 * Question route with contradicted evidence.
 * CONTRADICTED claims are withheld from answer but present in claims.
 */
export const questionRouteWithContradictionResponse: FactCheckedResponse = {
  input_type: "question",
  question: "Does RISC have a complex instruction set?",
  answer: "The evidence suggests otherwise. [C1]",
  state: "ok",
  claims: [contradictedClaim],
  retrieved_chunk_ids: ["1:3"],
  latency_ms: {
    retrieval_ms: 100,
    rerank_ms: 50,
    generation_ms: 200,
    verification_ms: 100,
  },
  rejected_claims: [contradictedClaim],
  refusals: [],
};

/**
 * Question route with refusal.
 * Refusal has null evidence_score and no citations.
 */
export const questionRouteWithRefusalResponse: FactCheckedResponse = {
  input_type: "question",
  question: "What is a sensitive topic?",
  answer: "[REFUSAL] I cannot provide information about this topic.",
  state: "ok",
  claims: [refusalClaim],
  retrieved_chunk_ids: ["1:3"],
  latency_ms: {
    retrieval_ms: 100,
    rerank_ms: 50,
    generation_ms: 200,
    verification_ms: 100,
  },
  rejected_claims: [],
  refusals: [refusalClaim],
};

/**
 * Fact route response - statement fact-checking.
 * - Exactly one claim
 * - Fixed canned answer sentence
 * - No generation_ms key (NO generation call on fact route)
 * - Citations list is "everything considered", not "everything that supports"
 */
export const factRouteSupportedResponse: FactCheckedResponse = {
  input_type: "fact",
  question: "RISC uses a reduced instruction set.",
  answer: "This statement is supported by the retrieved evidence.",
  state: "ok",
  claims: [
    {
      text: "RISC uses a reduced instruction set.",
      status: "SUPPORTED",
      citations: ["C1", "C2", "C3"],
      evidence_score: 0.92,
      chunk_ids: ["1:3", "1:5", "1:6"],
      reason: null,
    },
  ],
  retrieved_chunk_ids: ["1:3", "1:5", "1:6"],
  latency_ms: {
    retrieval_ms: 100,
    rerank_ms: 50,
    verification_ms: 100,
    // NOTE: NO generation_ms key on fact route
  },
  rejected_claims: [],
  refusals: [],
};

/**
 * Fact route - statement is contradicted.
 */
export const factRouteContradictedResponse: FactCheckedResponse = {
  input_type: "fact",
  question: "RISC has a complex instruction set.",
  answer: "This statement is contradicted by the retrieved evidence.",
  state: "contradicted",
  claims: [
    {
      text: "RISC has a complex instruction set.",
      status: "CONTRADICTED",
      citations: ["C1", "C2"],
      evidence_score: 0.15,
      chunk_ids: ["1:3", "1:5"],
      reason: "The evidence clearly states that RISC uses a reduced instruction set",
    },
  ],
  retrieved_chunk_ids: ["1:3", "1:5"],
  latency_ms: {
    retrieval_ms: 100,
    rerank_ms: 50,
    verification_ms: 100,
    // NO generation_ms
  },
  rejected_claims: [],
  refusals: [],
};

/**
 * Fact route - insufficient evidence.
 */
export const factRouteInsufficientResponse: FactCheckedResponse = {
  input_type: "fact",
  question: "Quantum computing uses exotic materials from Mars.",
  answer: "There is insufficient evidence to evaluate this statement.",
  state: "insufficient_evidence",
  claims: [
    {
      text: "Quantum computing uses exotic materials from Mars.",
      status: "UNSUPPORTED",
      citations: ["C1"],
      evidence_score: 0.21,
      chunk_ids: ["1:3"],
      reason: "The evidence does not address this statement",
    },
  ],
  retrieved_chunk_ids: ["1:3"],
  latency_ms: {
    retrieval_ms: 100,
    rerank_ms: 50,
    verification_ms: 100,
  },
  rejected_claims: [],
  refusals: [],
};

/**
 * Multiple claim types in one response.
 */
export const mixedClaimsResponse: FactCheckedResponse = {
  input_type: "question",
  question: "Tell me about RISC architecture",
  answer:
    "RISC uses a reduced instruction set [C1]. Modern processors implement RISC [C2]. Some systems require complex instruction sets [C3].",
  state: "ok",
  claims: [
    supportedClaim,
    {
      text: "Modern processors implement RISC",
      status: "SUPPORTED",
      citations: ["C2"],
      evidence_score: 0.88,
      chunk_ids: ["1:5"],
      reason: null,
    },
    {
      text: "Some systems require complex instruction sets",
      status: "UNSUPPORTED",
      citations: ["C3"],
      evidence_score: 0.35,
      chunk_ids: ["1:6"],
      reason: "This is a generalization not supported by the evidence",
    },
  ],
  retrieved_chunk_ids: ["1:3", "1:5", "1:6"],
  latency_ms: {
    retrieval_ms: 100,
    rerank_ms: 50,
    generation_ms: 200,
    verification_ms: 100,
  },
  rejected_claims: [
    {
      text: "Some systems require complex instruction sets",
      status: "UNSUPPORTED",
      citations: ["C3"],
      evidence_score: 0.35,
      chunk_ids: ["1:6"],
      reason: "This is a generalization not supported by the evidence",
    },
  ],
  refusals: [],
};

/**
 * Response with many latency keys (question route).
 */
export const responseWithCompleteLatency: FactCheckedResponse = {
  input_type: "question",
  question: "What is pipelining?",
  answer: "Pipelining is an instruction execution technique [C1]",
  state: "ok",
  claims: [supportedClaim],
  retrieved_chunk_ids: ["1:3"],
  latency_ms: {
    retrieval_ms: 2810.4,
    rerank_ms: 1180.2,
    generation_ms: 1700.5,
    verification_ms: 450.1,
    model_load_ms: 600.8,
  },
  rejected_claims: [],
  refusals: [],
};

/**
 * Fact route response with minimal latency keys.
 */
export const factRouteMinimalLatency: FactCheckedResponse = {
  input_type: "fact",
  question: "RISC has instruction pipelining.",
  answer: "This statement is supported by the retrieved evidence.",
  state: "ok",
  claims: [supportedClaim],
  retrieved_chunk_ids: ["1:3"],
  latency_ms: {
    retrieval_ms: 100,
    verification_ms: 150,
  },
  rejected_claims: [],
  refusals: [],
};

/**
 * Response with unknown/made-up latency keys (robustness test).
 */
export const responseWithUnknownLatencyKeys: FactCheckedResponse = {
  input_type: "question",
  question: "What is RISC?",
  answer: "RISC uses a reduced instruction set [C1]",
  state: "ok",
  claims: [supportedClaim],
  retrieved_chunk_ids: ["1:3"],
  latency_ms: {
    retrieval_ms: 100,
    custom_metric_ms: 50,
    another_unknown_stage_ms: 200,
    generation_ms: 100,
  },
  rejected_claims: [],
  refusals: [],
};

/**
 * Streaming response events for token-rendering safety test.
 */
export const streamingTokenEvents = [
  {
    event: "retrieval" as const,
    data: { retrieved_chunk_ids: ["1:3", "1:5"] },
  },
  {
    event: "token" as const,
    data: {
      text: '{"kind":"claim","text":"SENTINEL_TOKEN_SHOULD_NOT_APPEAR"',
    },
  },
  {
    event: "token" as const,
    data: {
      text: ',"status":"SUPPORTED"}',
    },
  },
  {
    event: "verification" as const,
    data: questionRouteOkResponse,
  },
  {
    event: "done" as const,
    data: {},
  },
];

/**
 * Fact route streaming - zero token events.
 */
export const factRouteStreamingEvents = [
  {
    event: "retrieval" as const,
    data: { retrieved_chunk_ids: ["1:3"] },
  },
  // NOTE: Zero token events on fact route
  {
    event: "verification" as const,
    data: factRouteSupportedResponse,
  },
  {
    event: "done" as const,
    data: {},
  },
];
