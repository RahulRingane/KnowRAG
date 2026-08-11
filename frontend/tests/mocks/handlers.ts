import { http, HttpResponse } from "msw";

export const API_BASE_URL = "http://localhost:8000";

/**
 * Shared MSW handlers for network mocking across all test suites.
 * Individual suites can override specific handlers; defaults enable
 * test setup without repetition.
 */

export const defaultHandlers = [
  // Health check — open endpoint
  http.get(`${API_BASE_URL}/health`, () => {
    return HttpResponse.json({
      status: "ok",
      service: "knowrag",
      checks: {
        postgres: { status: "ok" },
        qdrant: { status: "ok" },
        elasticsearch: { status: "ok" },
      },
    });
  }),

  // Auth endpoints — default success responses
  http.post(`${API_BASE_URL}/auth/login`, () => {
    return HttpResponse.json(
      {
        access_token: "test-access-token",
        token_type: "bearer",
        expires_in: 900,
      },
      {
        headers: {
          "Set-Cookie": "knowrag_refresh=test-refresh; HttpOnly; SameSite=Lax; Path=/auth",
        },
      }
    );
  }),

  http.post(`${API_BASE_URL}/auth/register`, () => {
    return HttpResponse.json({
      id: 1,
      username: "testuser",
      created_at: "2026-08-11T00:00:00Z",
    });
  }),

  http.post(`${API_BASE_URL}/auth/refresh`, () => {
    return HttpResponse.json({
      access_token: "test-access-token-refreshed",
      token_type: "bearer",
      expires_in: 900,
    });
  }),

  http.post(`${API_BASE_URL}/auth/logout`, () => {
    return new HttpResponse(null, {
      status: 204,
      headers: {
        "Set-Cookie": "knowrag_refresh=; HttpOnly; SameSite=Lax; Path=/auth; Max-Age=0",
      },
    });
  }),

  http.get(`${API_BASE_URL}/auth/me`, () => {
    return HttpResponse.json({
      id: 1,
      username: "testuser",
      created_at: "2026-08-11T00:00:00Z",
    });
  }),

  // Query endpoint — default success response
  http.post(`${API_BASE_URL}/query`, () => {
    return HttpResponse.json({
      input_type: "question",
      question: "What is RISC?",
      answer: "RISC uses a reduced instruction set [C1]",
      state: "ok",
      claims: [
        {
          text: "RISC uses a reduced instruction set",
          status: "SUPPORTED",
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
    });
  }),

  // Ingest endpoints — default success responses
  http.post(`${API_BASE_URL}/ingest`, () => {
    return HttpResponse.json(
      {
        document_id: 1,
        filename: "test.pdf",
        status: "pending",
      },
      { status: 202 }
    );
  }),

  http.get(`${API_BASE_URL}/ingest/:documentId`, () => {
    return HttpResponse.json({
      document_id: 1,
      filename: "test.pdf",
      status: "indexed",
      chunk_count: 10,
      ingested_at: "2026-08-11T10:00:00Z",
      error: null,
    });
  }),

  // Documents list endpoint
  http.get(`${API_BASE_URL}/documents`, () => {
    return HttpResponse.json([]);
  }),

  // Chunks endpoint
  http.get(`${API_BASE_URL}/chunks`, () => {
    return HttpResponse.json([]);
  }),
];
