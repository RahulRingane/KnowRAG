import { env } from "@/config/env";
import { refreshAccessToken } from "@/lib/auth/refresh";
import { getAccessToken } from "@/lib/auth/token-store";
import {
  StreamDoneDataSchema,
  StreamErrorDataSchema,
  StreamRetrievalDataSchema,
  StreamTokenDataSchema,
  StreamVerificationDataSchema,
} from "@/lib/schemas/stream";
import type { QueryRequest, StreamEvent } from "@/types/api";
import { ApiError } from "@/types/api";

import { buildApiErrorFromResponse } from "./errors";

/**
 * `POST /query/stream` transport. `EventSource` is unusable here — it is
 * GET-only, and this route is a POST (plan §1.2) — so this hand-rolls the
 * transport with `fetch` + `ReadableStream` and a hand-written SSE frame
 * parser instead.
 */

const STREAM_PATH = "/query/stream";

interface ParsedFrame {
  event: string;
  data: string;
}

/**
 * Accumulates raw decoded text across chunks and slices out complete
 * `event:`/`data:` frames, each terminated by a blank line (`\n\n`, the SSE
 * frame boundary). This is the piece that's easy to get subtly wrong:
 *
 * - **Multiple frames in one chunk**: `push()` loops on `indexOf("\n\n")`,
 *   draining every complete frame the chunk contains before returning.
 * - **A single frame split across chunks**: any text after the *last*
 *   `\n\n` found so far is left in `this.buffer` rather than parsed —
 *   it's retained and prefixed onto whatever the next `push()` call
 *   receives, so a frame that arrives half in one TCP chunk and half in
 *   the next is only parsed once it's actually complete.
 * - **A trailing partial buffer**: if the stream ends (`reader.read()`
 *   resolves `done: true`) with leftover, non-blank text in the buffer —
 *   a server that doesn't terminate its final frame with a blank line —
 *   `flush()` parses whatever's left instead of silently dropping it.
 *
 * Parsing per-chunk instead of per-accumulated-buffer (a tempting
 * shortcut) would silently truncate or drop whichever frame happens to
 * straddle a chunk boundary — chunk boundaries are a TCP/HTTP framing
 * detail with no relationship to where the server happened to flush an
 * SSE frame.
 */
class SSEFrameBuffer {
  private buffer = "";

  push(chunk: string): ParsedFrame[] {
    this.buffer += chunk;
    const frames: ParsedFrame[] = [];
    let boundary = this.buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const raw = this.buffer.slice(0, boundary);
      this.buffer = this.buffer.slice(boundary + 2);
      const frame = parseFrame(raw);
      if (frame) frames.push(frame);
      boundary = this.buffer.indexOf("\n\n");
    }
    return frames;
  }

  /** Call once after the stream closes, to recover a final frame the
   *  server sent without a trailing blank-line terminator. */
  flush(): ParsedFrame[] {
    if (this.buffer.trim().length === 0) return [];
    const frame = parseFrame(this.buffer);
    this.buffer = "";
    return frame ? [frame] : [];
  }
}

function parseFrame(raw: string): ParsedFrame | null {
  let eventName = "message"; // SSE default when no `event:` line is present
  const dataLines: string[] = [];
  for (const line of raw.split("\n")) {
    if (line === "" || line.startsWith(":")) continue; // blank line / SSE comment
    if (line.startsWith("event:")) {
      eventName = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trimStart());
    }
  }
  if (dataLines.length === 0) return null; // keep-alive / comment-only frame
  // Per the SSE spec, multiple `data:` lines in one frame join with `\n`.
  return { event: eventName, data: dataLines.join("\n") };
}

function toStreamEvent(frame: ParsedFrame): StreamEvent {
  let payload: unknown;
  try {
    payload = JSON.parse(frame.data);
  } catch {
    throw new Error(`SSE frame for event "${frame.event}" was not valid JSON: ${frame.data}`);
  }

  switch (frame.event) {
    case "retrieval":
      return { event: "retrieval", data: StreamRetrievalDataSchema.parse(payload) };
    case "token":
      return { event: "token", data: StreamTokenDataSchema.parse(payload) };
    case "verification":
      return { event: "verification", data: StreamVerificationDataSchema.parse(payload) };
    case "done":
      return { event: "done", data: StreamDoneDataSchema.parse(payload) };
    case "error":
      return { event: "error", data: StreamErrorDataSchema.parse(payload) };
    default:
      throw new Error(`Unknown SSE event type "${frame.event}"`);
  }
}

export interface StreamQueryOptions {
  signal?: AbortSignal;
}

/**
 * Opens `/query/stream` and yields typed `StreamEvent`s as they arrive:
 * `retrieval` → zero or more `token` → `verification` → `done`, or an
 * `error` frame at any point. A fact-route input emits zero `token` events
 * (plan §1.2).
 *
 * **`token` payloads are raw JSON fragments of the claim schema, NOT
 * prose.** The backend's own docs and CLAUDE.md are explicit: nothing
 * before the terminal `verification` event has been fact-checked. Callers
 * (WS-D) must only use `token` events to drive a live activity indicator
 * ("generating claim 3…") — never render `.data.text` as answer content.
 *
 * Auth: a stream can't be "retried" once bytes have started arriving, so
 * refresh must happen *before* the connection opens, not after a 401 seen
 * mid-stream. `fetch()` resolves the response (headers + status) before
 * the body starts streaming, so a `401` is always caught here prior to any
 * event being yielded — this refreshes once and re-opens the connection
 * once, always pre-first-byte. If a 401 were ever detected after events
 * had already been emitted to the caller, this throws instead of silently
 * reconnecting (plan §3.2): a partially consumed, only-partially-verified
 * stream must never be presented as if it restarted cleanly.
 */
export async function* streamQuery(
  body: QueryRequest,
  options: StreamQueryOptions = {},
): AsyncGenerator<StreamEvent, void, void> {
  let emittedAny = false;

  for (let attempt = 0; attempt <= 1; attempt++) {
    const token = getAccessToken();
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    };
    if (token) headers.Authorization = `Bearer ${token}`;

    let response: Response;
    try {
      response = await fetch(new URL(STREAM_PATH, env.apiBaseUrl), {
        method: "POST",
        headers,
        body: JSON.stringify(body),
        signal: options.signal,
      });
    } catch (error) {
      throw new ApiError(
        0,
        `Network error opening ${STREAM_PATH}: ${error instanceof Error ? error.message : String(error)}`,
      );
    }

    if (!response.ok) {
      if (response.status === 401 && attempt === 0 && !emittedAny) {
        const newToken = await refreshAccessToken();
        if (newToken) continue; // re-open once, still before any bytes were read
      }
      throw await buildApiErrorFromResponse(response);
    }

    if (!response.body) {
      throw new ApiError(0, `${STREAM_PATH} returned a success status but no readable body.`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    const frameBuffer = new SSEFrameBuffer();

    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) {
          for (const frame of frameBuffer.flush()) {
            emittedAny = true;
            yield toStreamEvent(frame);
          }
          return;
        }
        const chunk = decoder.decode(value, { stream: true });
        for (const frame of frameBuffer.push(chunk)) {
          emittedAny = true;
          yield toStreamEvent(frame);
        }
      }
    } finally {
      reader.releaseLock();
    }
  }
}
