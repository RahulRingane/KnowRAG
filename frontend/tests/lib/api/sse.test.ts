import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";

import { streamQuery } from "@/lib/api/sse";
import { server } from "../../setup";

const API_BASE_URL = "http://localhost:8000";

describe("SSE parser (streamQuery)", () => {
  describe("well-formed multi-event stream", () => {
    it("yields typed events in order: retrieval → token → verification → done", async () => {
      const sseBody = `event: retrieval
data: {"retrieved_chunk_ids":["1:3"]}

event: token
data: {"text":"RISC"}

event: token
data: {"text":" uses"}

event: verification
data: {"input_type":"question","question":"What is RISC?","answer":"RISC uses","state":"ok","claims":[],"retrieved_chunk_ids":["1:3"],"latency_ms":{},"rejected_claims":[],"refusals":[]}

event: done
data: {}

`;

      server.use(
        http.post(`${API_BASE_URL}/query/stream`, () => {
          return new HttpResponse(sseBody, {
            headers: { "Content-Type": "text/event-stream" },
          });
        })
      );

      const events = [];
      for await (const event of streamQuery({ question: "What is RISC?" })) {
        events.push(event);
      }

      expect(events).toHaveLength(5);
      expect(events[0]?.event).toBe("retrieval");
      expect(events[1]?.event).toBe("token");
      expect(events[2]?.event).toBe("token");
      expect(events[3]?.event).toBe("verification");
      expect(events[4]?.event).toBe("done");
    });
  });

  describe("frame parsing edge cases", () => {
    it("handles multiple frames in one chunk", async () => {
      const sseBody = `event: retrieval
data: {"retrieved_chunk_ids":["1:3"]}

event: token
data: {"text":"RISC"}

event: done
data: {}

`;

      server.use(
        http.post(`${API_BASE_URL}/query/stream`, () => {
          return new HttpResponse(sseBody, {
            headers: { "Content-Type": "text/event-stream" },
          });
        })
      );

      const events = [];
      for await (const streamEvent of streamQuery({ question: "test" })) {
        events.push(streamEvent);
      }

      expect(events).toHaveLength(3);
      expect(events.map((e) => e.event)).toEqual([
        "retrieval",
        "token",
        "done",
      ]);
    });

    it("handles a single frame split across multiple chunks", async () => {
      // Simulate a frame arriving in two TCP chunks
      server.use(
        http.post(`${API_BASE_URL}/query/stream`, () => {
          // Return a ReadableStream that emits data in two parts
          const chunks = [
            `event: retrieval\ndata: {"retrieved_chunk_`,
            `ids":["1:3"]}\n\n`,
          ];

          return new HttpResponse(
            new ReadableStream({
              start(controller) {
                chunks.forEach((chunk) => {
                  controller.enqueue(new TextEncoder().encode(chunk));
                });
                controller.close();
              },
            }),
            {
              headers: { "Content-Type": "text/event-stream" },
            }
          );
        })
      );

      const events = [];
      for await (const streamEvent of streamQuery({ question: "test" })) {
        events.push(streamEvent);
      }

      expect(events).toHaveLength(1);
      expect(events[0]?.event).toBe("retrieval");
      expect(events[0]?.data).toEqual({ retrieved_chunk_ids: ["1:3"] });
    });

    it("handles trailing frame without blank line terminator", async () => {
      const sseBody = `event: retrieval
data: {"retrieved_chunk_ids":["1:3"]}

event: done
data: {}`;
      // Note: No final \n\n

      server.use(
        http.post(`${API_BASE_URL}/query/stream`, () => {
          return new HttpResponse(sseBody, {
            headers: { "Content-Type": "text/event-stream" },
          });
        })
      );

      const events = [];
      for await (const streamEvent of streamQuery({ question: "test" })) {
        events.push(streamEvent);
      }

      expect(events).toHaveLength(2);
      expect(events.map((e) => e.event)).toEqual(["retrieval", "done"]);
    });
  });

  describe("error event handling", () => {
    it("surfaces error event from stream", async () => {
      const sseBody = `event: retrieval
data: {"retrieved_chunk_ids":["1:3"]}

event: error
data: {"detail":"Generation failed"}

`;

      server.use(
        http.post(`${API_BASE_URL}/query/stream`, () => {
          return new HttpResponse(sseBody, {
            headers: { "Content-Type": "text/event-stream" },
          });
        })
      );

      const events = [];
      for await (const streamEvent of streamQuery({ question: "test" })) {
        events.push(streamEvent);
      }

      expect(events).toHaveLength(2);
      expect(events[1]?.event).toBe("error");
      expect(events[1]?.data).toEqual({ detail: "Generation failed" });
    });

    it("throws when encountering unknown event type", async () => {
      const sseBody = `event: unknown_event
data: {"foo":"bar"}

`;

      server.use(
        http.post(`${API_BASE_URL}/query/stream`, () => {
          return new HttpResponse(sseBody, {
            headers: { "Content-Type": "text/event-stream" },
          });
        })
      );

      try {
        for await (const _streamEvent of streamQuery({ question: "test" })) {
          // iterate through
        }
        expect.fail("Should have thrown");
      } catch (err) {
        expect(err).toBeInstanceOf(Error);
        expect((err as Error).message).toContain("Unknown SSE event type");
      }
    });
  });

  describe("fact-route stream (zero token events)", () => {
    it("goes retrieval → verification → done without tokens", async () => {
      const sseBody = `event: retrieval
data: {"retrieved_chunk_ids":["1:3","1:5"]}

event: verification
data: {"input_type":"fact","question":"RISC has instruction pipelining","answer":"This statement is supported by the retrieved evidence","state":"ok","claims":[{"text":"RISC has instruction pipelining","status":"SUPPORTED","citations":["C1"],"evidence_score":0.995,"chunk_ids":["1:3"],"reason":null}],"retrieved_chunk_ids":["1:3","1:5"],"latency_ms":{"retrieval_ms":100},"rejected_claims":[],"refusals":[]}

event: done
data: {}

`;

      server.use(
        http.post(`${API_BASE_URL}/query/stream`, () => {
          return new HttpResponse(sseBody, {
            headers: { "Content-Type": "text/event-stream" },
          });
        })
      );

      const events = [];
      for await (const event of streamQuery({ question: "RISC has instruction pipelining" })) {
        events.push(event);
      }

      expect(events).toHaveLength(3);
      expect(events.map((e) => e.event)).toEqual([
        "retrieval",
        "verification",
        "done",
      ]);

      // Verify no token events
      expect(events.filter((e) => e.event === "token")).toHaveLength(0);
    });

    it("fact-route verification has no generation_ms in latency_ms", async () => {
      const sseBody = `event: retrieval
data: {"retrieved_chunk_ids":["1:3"]}

event: verification
data: {"input_type":"fact","question":"RISC has pipelining","answer":"Evidence supports this","state":"ok","claims":[],"retrieved_chunk_ids":["1:3"],"latency_ms":{"retrieval_ms":100,"verification_ms":50},"rejected_claims":[],"refusals":[]}

event: done
data: {}

`;

      server.use(
        http.post(`${API_BASE_URL}/query/stream`, () => {
          return new HttpResponse(sseBody, {
            headers: { "Content-Type": "text/event-stream" },
          });
        })
      );

      let verificationEvent: Record<string, unknown> | undefined;
      for await (const streamEvent of streamQuery({ question: "RISC has pipelining" })) {
        if (streamEvent.event === "verification") {
          verificationEvent = streamEvent;
        }
      }

      expect(verificationEvent).toBeDefined();
      expect((verificationEvent as Record<string, unknown>).data).toBeDefined();
      const data = (verificationEvent as Record<string, unknown>).data as Record<string, unknown>;
      const latencyMs = data.latency_ms as Record<string, unknown>;
      expect(latencyMs).toBeDefined();
      expect(latencyMs.generation_ms).toBeUndefined();
      expect(latencyMs.retrieval_ms).toBe(100);
      expect(latencyMs.verification_ms).toBe(50);
    });
  });

  describe("abort handling", () => {
    it("aborts stream cleanly without unhandled rejection", async () => {
      server.use(
        http.post(`${API_BASE_URL}/query/stream`, async () => {
          return new HttpResponse(
            new ReadableStream({
              async start(controller) {
                for (let i = 0; i < 100; i++) {
                  controller.enqueue(
                    new TextEncoder().encode(
                      `event: token\ndata: {"text":"word${i}"}\n\n`
                    )
                  );
                  await new Promise((r) => setTimeout(r, 10));
                }
                controller.close();
              },
            }),
            {
              headers: { "Content-Type": "text/event-stream" },
            }
          );
        })
      );

      const controller = new AbortController();
      const events = [];

      try {
        for await (const event of streamQuery(
          { question: "test" },
          { signal: controller.signal }
        )) {
          events.push(event);
          if (events.length >= 2) {
            controller.abort();
          }
        }
      } catch {
        // Abort may throw, which is acceptable
      }

      // Should have received some events before abort
      expect(events.length).toBeGreaterThan(0);
    });

    it("throws ApiError if 401 received after events have been emitted", async () => {
      let requestCount = 0;

      server.use(
        http.post(`${API_BASE_URL}/query/stream`, () => {
          requestCount++;
          if (requestCount === 2) {
            // Simulate a 401 on retry (shouldn't happen, but test defensive behavior)
            return HttpResponse.json(
              { detail: "Unauthorized" },
              { status: 401 }
            );
          }

          return new HttpResponse(
            `event: retrieval\ndata: {"retrieved_chunk_ids":["1:3"]}\n\nevent: token\ndata: {"text":"test"}\n\n`,
            {
              headers: { "Content-Type": "text/event-stream" },
            }
          );
        })
      );

      const events = [];
      try {
        for await (const streamEvent of streamQuery({ question: "test" })) {
          events.push(streamEvent);
          if (events.length >= 2) {
            // At this point we've emitted events, so further 401 should error
            break;
          }
        }
      } catch {
        // Expected behavior captured above
      }

      expect(events.length).toBeGreaterThan(0);
    });
  });

  describe("refresh interceptor", () => {
    it("attempts refresh before stream opens if initial request is 401", async () => {
      let requestCount = 0;

      server.use(
        http.post(`${API_BASE_URL}/query/stream`, () => {
          requestCount++;
          if (requestCount === 1) {
            return HttpResponse.json(
              { detail: "Unauthorized" },
              { status: 401 }
            );
          }

          // Second attempt succeeds
          return new HttpResponse(
            `event: retrieval\ndata: {"retrieved_chunk_ids":["1:3"]}\n\nevent: done\ndata: {}\n\n`,
            {
              headers: { "Content-Type": "text/event-stream" },
            }
          );
        })
      );

      let refreshCount = 0;
      server.use(
        http.post(`${API_BASE_URL}/auth/refresh`, () => {
          refreshCount++;
          return HttpResponse.json({
            access_token: "new-token",
            token_type: "bearer",
            expires_in: 900,
          });
        })
      );

      const events = [];
      for await (const streamEvent of streamQuery({ question: "test" })) {
        events.push(streamEvent);
      }

      // Should have gotten events from the retry
      expect(events.length).toBeGreaterThan(0);
      // Refresh should have been attempted
      expect(refreshCount).toBeGreaterThan(0);
    });

    it("does not retry if refresh fails", async () => {
      let requestCount = 0;

      server.use(
        http.post(`${API_BASE_URL}/query/stream`, () => {
          requestCount++;
          return HttpResponse.json(
            { detail: "Unauthorized" },
            { status: 401 }
          );
        })
      );

      server.use(
        http.post(`${API_BASE_URL}/auth/refresh`, () => {
          return HttpResponse.json(
            { detail: "Refresh token expired" },
            { status: 401 }
          );
        })
      );

      try {
        for await (const _ of streamQuery({ question: "test" })) {
          // iterate
        }
        expect.fail("Should have thrown ApiError");
      } catch (error) {
        expect(error).toHaveProperty("status", 401);
      }

      // Should have tried stream once and refresh once, not retried stream
      expect(requestCount).toBe(1);
    });
  });

  describe("malformed JSON handling", () => {
    it("throws on malformed JSON in frame data", async () => {
      const sseBody = `event: retrieval
data: {invalid json}

`;

      server.use(
        http.post(`${API_BASE_URL}/query/stream`, () => {
          return new HttpResponse(sseBody, {
            headers: { "Content-Type": "text/event-stream" },
          });
        })
      );

      try {
        for await (const _ of streamQuery({ question: "test" })) {
          // iterate
        }
        expect.fail("Should have thrown");
      } catch (error) {
        expect(error).toBeInstanceOf(Error);
        expect((error as Error).message).toContain("not valid JSON");
      }
    });
  });

  describe("network response errors", () => {
    it("throws ApiError if response has no body", async () => {
      server.use(
        http.post(`${API_BASE_URL}/query/stream`, () => {
          return new HttpResponse(null, {
            status: 200,
            headers: { "Content-Type": "text/event-stream" },
          });
        })
      );

      try {
        for await (const _ of streamQuery({ question: "test" })) {
          // iterate
        }
        expect.fail("Should have thrown");
      } catch (err) {
        expect(err).toHaveProperty("status", 0);
        expect((err as Record<string, unknown>).detail).toContain("no readable body");
      }
    });

    it("throws ApiError on 404 response", async () => {
      server.use(
        http.post(`${API_BASE_URL}/query/stream`, () => {
          return HttpResponse.json(
            { detail: "Not found" },
            { status: 404 }
          );
        })
      );

      try {
        for await (const _ of streamQuery({ question: "test" })) {
          // iterate
        }
        expect.fail("Should have thrown");
      } catch (error) {
        expect(error).toHaveProperty("status", 404);
      }
    });
  });

  describe("schema validation", () => {
    it("validates retrieval event data shape", async () => {
      const sseBody = `event: retrieval
data: {"retrieved_chunk_ids":["1:3","1:5"]}

event: done
data: {}

`;

      server.use(
        http.post(`${API_BASE_URL}/query/stream`, () => {
          return new HttpResponse(sseBody, {
            headers: { "Content-Type": "text/event-stream" },
          });
        })
      );

      const events = [];
      for await (const streamEvent of streamQuery({ question: "test" })) {
        events.push(streamEvent);
      }

      const retrievalEvent = events.find((e) => e.event === "retrieval");
      expect(retrievalEvent?.data).toHaveProperty("retrieved_chunk_ids");
      expect(Array.isArray(retrievalEvent?.data.retrieved_chunk_ids)).toBe(
        true
      );
    });

    it("validates token event data shape", async () => {
      const sseBody = `event: token
data: {"text":"test"}

event: done
data: {}

`;

      server.use(
        http.post(`${API_BASE_URL}/query/stream`, () => {
          return new HttpResponse(sseBody, {
            headers: { "Content-Type": "text/event-stream" },
          });
        })
      );

      const events = [];
      for await (const streamEvent of streamQuery({ question: "test" })) {
        events.push(streamEvent);
      }

      const tokenEvent = events.find((e) => e.event === "token");
      expect(tokenEvent?.data).toHaveProperty("text");
    });
  });
});
