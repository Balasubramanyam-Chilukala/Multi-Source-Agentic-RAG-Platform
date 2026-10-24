import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./client";
import { SseParser, streamChat, toStreamEvent } from "./sse";
import type { StreamEvent } from "./types";

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(encoder.encode(c));
      controller.close();
    },
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("SseParser", () => {
  it("parses complete events with event names and JSON data", () => {
    const parser = new SseParser();
    const events = parser.push('event: meta\ndata: {"conversation_id":"c1","user_message_id":"m1"}\n\nevent: token\ndata: {"text":"Hi"}\n\n');
    expect(events).toEqual([
      { event: "meta", data: '{"conversation_id":"c1","user_message_id":"m1"}' },
      { event: "token", data: '{"text":"Hi"}' },
    ]);
  });

  it("reassembles events split across arbitrary chunk boundaries", () => {
    const raw = 'event: token\ndata: {"text":"Hello"}\n\nevent: token\ndata: {"text":" world"}\n\n';
    // Feed one character at a time — the worst case for a streaming parser.
    const parser = new SseParser();
    const events = [...raw].flatMap((ch) => parser.push(ch));
    expect(events.map((e) => JSON.parse(e.data).text)).toEqual(["Hello", " world"]);
  });

  it("handles CRLF line endings even when \\r and \\n arrive in different chunks", () => {
    const parser = new SseParser();
    const events = [...parser.push("event: step\r"), ...parser.push('\ndata: {"node":"retrieve","status":"started"}\r\n\r'), ...parser.push("\n")];
    expect(events).toEqual([{ event: "step", data: '{"node":"retrieve","status":"started"}' }]);
  });

  it("ignores ':' keep-alive comments, including between and inside events", () => {
    const parser = new SseParser();
    const events = parser.push(': keep-alive\n\nevent: token\n: still here\ndata: {"text":"a"}\n\n: keep-alive\n\n');
    expect(events).toEqual([{ event: "token", data: '{"text":"a"}' }]);
  });

  it("joins multi-line data with newlines and defaults the event name to 'message'", () => {
    const parser = new SseParser();
    expect(parser.push("data: line1\ndata: line2\n\n")).toEqual([{ event: "message", data: "line1\nline2" }]);
  });

  it("flushes a trailing event that was not terminated by a blank line", () => {
    const parser = new SseParser();
    expect(parser.push('event: final\ndata: {"x":1}')).toEqual([]);
    expect(parser.flush()).toEqual([{ event: "final", data: '{"x":1}' }]);
  });

  it("drops unknown event types and malformed JSON", () => {
    expect(toStreamEvent({ event: "ping", data: "{}" })).toBeNull();
    expect(toStreamEvent({ event: "token", data: "{not json" })).toBeNull();
    expect(toStreamEvent({ event: "token_reset", data: "{}" })).toEqual({ event: "token_reset", data: {} });
  });
});

describe("streamChat", () => {
  it("delivers typed events in order from a chunked response body", async () => {
    const body = streamOf([
      'event: meta\ndata: {"conversation_id":"c1","user_message_id":"u1"}\n',
      '\n: keep-alive\n\nevent: token\ndata: {"te',
      'xt":"draft"}\n\nevent: token_reset\ndata: {}\n\nevent: token\ndata: {"text":"again"}\n\n',
      'event: final\ndata: {"conversation_id":"c1","user_message_id":"u1","message":{"id":"a1","content":"Final [S1]"}}\n\n',
    ]);
    const fetchMock = vi.fn(async () => new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }));
    vi.stubGlobal("fetch", fetchMock);

    const events: StreamEvent[] = [];
    await streamChat({ message: "q" }, { onEvent: (e) => events.push(e) });

    expect(events.map((e) => e.event)).toEqual(["meta", "token", "token_reset", "token", "final"]);
    expect(events[1]).toEqual({ event: "token", data: { text: "draft" } });
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ message: "q" });
  });

  it("rejects with an ApiError carrying Retry-After on 429", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ error: { code: "rate_limited", message: "Too many requests; please slow down.", request_id: "r-9" } }), {
          status: 429,
          headers: { "Content-Type": "application/json", "Retry-After": "12" },
        }),
      ),
    );
    const error = await streamChat({ message: "q" }, { onEvent: () => {} }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 429, retryAfter: 12, requestId: "r-9", message: "Too many requests; please slow down." });
  });
});
