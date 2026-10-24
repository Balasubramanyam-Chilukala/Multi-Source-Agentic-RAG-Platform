/**
 * Server-Sent Events over a POST request. EventSource only supports GET, so the chat stream is
 * read with fetch + ReadableStream and parsed here following the WHATWG event-stream rules:
 * events are separated by a blank line, `event:` names the event, `data:` lines are joined with
 * "\n", and lines starting with ":" (keep-alive comments) are ignored.
 */
import { authHeaders, buildUrl, handleUnauthorized, networkError, toApiError } from "./client";
import type { ChatRequest, StreamEvent } from "./types";

export interface RawSseEvent {
  event: string;
  data: string;
}

export class SseParser {
  private buffer = "";
  private eventName = "";
  private dataLines: string[] = [];

  /** Feed a decoded text chunk; returns every event completed by it. */
  push(chunk: string): RawSseEvent[] {
    this.buffer += chunk;
    const events: RawSseEvent[] = [];
    // Normalize CRLF / CR line endings, but keep a trailing "\r" in the buffer in case the
    // matching "\n" arrives in the next chunk.
    let newline: number;
    while ((newline = this.findLineEnd()) !== -1) {
      const line = this.buffer.slice(0, newline);
      const skip = this.buffer[newline] === "\r" && this.buffer[newline + 1] === "\n" ? 2 : 1;
      this.buffer = this.buffer.slice(newline + skip);
      const event = this.processLine(line);
      if (event) events.push(event);
    }
    return events;
  }

  /** Dispatch a final event that was not followed by a blank line (stream ended). */
  flush(): RawSseEvent[] {
    const events: RawSseEvent[] = [];
    if (this.buffer) {
      const event = this.processLine(this.buffer);
      this.buffer = "";
      if (event) events.push(event);
    }
    const last = this.dispatch();
    if (last) events.push(last);
    return events;
  }

  private findLineEnd(): number {
    for (let i = 0; i < this.buffer.length; i++) {
      const ch = this.buffer[i];
      if (ch === "\n") return i;
      if (ch === "\r") {
        // A lone "\r" at the very end may be the first half of "\r\n": wait for more input.
        return i + 1 < this.buffer.length ? i : -1;
      }
    }
    return -1;
  }

  private processLine(line: string): RawSseEvent | null {
    if (line === "") return this.dispatch();
    if (line.startsWith(":")) return null; // comment / keep-alive
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") this.eventName = value;
    else if (field === "data") this.dataLines.push(value);
    // "id" and "retry" are not used by this API.
    return null;
  }

  private dispatch(): RawSseEvent | null {
    if (this.dataLines.length === 0) {
      this.eventName = "";
      return null;
    }
    const event = { event: this.eventName || "message", data: this.dataLines.join("\n") };
    this.eventName = "";
    this.dataLines = [];
    return event;
  }
}

const KNOWN_EVENTS = new Set(["meta", "step", "token", "token_reset", "final", "error"]);

/** Parse a raw event's JSON payload into a typed StreamEvent (unknown events are dropped). */
export function toStreamEvent(raw: RawSseEvent): StreamEvent | null {
  if (!KNOWN_EVENTS.has(raw.event)) return null;
  let data: unknown = {};
  if (raw.data) {
    try {
      data = JSON.parse(raw.data);
    } catch {
      return null;
    }
  }
  return { event: raw.event, data } as StreamEvent;
}

export interface StreamChatOptions {
  signal?: AbortSignal;
  onEvent: (event: StreamEvent) => void;
}

/**
 * POST /api/v1/chat/stream and deliver parsed events. Resolves when the stream ends; rejects
 * with an ApiError for HTTP errors (e.g. 429 with retryAfter), network failures, or abort.
 */
export async function streamChat(body: ChatRequest, { signal, onEvent }: StreamChatOptions): Promise<void> {
  let response: Response;
  try {
    response = await fetch(buildUrl("/api/v1/chat/stream"), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream", ...authHeaders() },
      body: JSON.stringify(body),
      signal,
    });
  } catch (cause) {
    throw networkError(cause);
  }
  if (!response.ok) {
    const error = await toApiError(response);
    if (response.status === 401) handleUnauthorized();
    throw error;
  }
  if (!response.body) throw networkError(new Error("empty body"));

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  const parser = new SseParser();
  const emit = (raws: RawSseEvent[]) => {
    for (const raw of raws) {
      const event = toStreamEvent(raw);
      if (event) onEvent(event);
    }
  };
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      emit(parser.push(value));
    }
    emit(parser.flush());
  } catch (cause) {
    throw networkError(cause);
  } finally {
    reader.releaseLock();
  }
}
