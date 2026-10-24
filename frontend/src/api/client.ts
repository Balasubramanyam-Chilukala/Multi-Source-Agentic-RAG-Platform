/**
 * Low-level HTTP client: base URL, bearer token, JSON handling, and the error envelope.
 *
 * Token storage trade-off: the access token is kept in memory and mirrored to localStorage so a
 * page reload keeps the session. localStorage is readable by any script on the origin, so an XSS
 * bug would expose the token; this is mitigated by never rendering raw HTML (react-markdown
 * without rehype-raw), a strict CSP in the nginx image, and short-lived tokens. An httpOnly
 * cookie would be stronger but the backend issues bearer tokens, not cookies.
 */
import type { ApiErrorBody } from "./types";

const TOKEN_KEY = "atlas.token";

export const API_BASE_URL: string = (
  (import.meta.env.VITE_API_BASE_URL as string | undefined) || "http://localhost:8000"
).replace(/\/+$/, "");

let memoryToken: string | null = readStoredToken();
let unauthorizedHandler: (() => void) | null = null;

function readStoredToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export const tokenStore = {
  get: (): string | null => memoryToken,
  set(token: string | null): void {
    memoryToken = token;
    try {
      if (token) localStorage.setItem(TOKEN_KEY, token);
      else localStorage.removeItem(TOKEN_KEY);
    } catch {
      /* storage unavailable (private mode): the in-memory token still works for this tab */
    }
  },
};

/** Registered by the auth provider: clears the session and redirects to the login page. */
export function onUnauthorized(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly details: Array<Record<string, unknown>>;
  /** Seconds to wait before retrying (from the Retry-After header on 429). */
  readonly retryAfter: number | null;

  constructor(init: {
    status: number;
    code: string;
    message: string;
    requestId?: string | null;
    details?: Array<Record<string, unknown>>;
    retryAfter?: number | null;
  }) {
    super(init.message);
    this.name = "ApiError";
    this.status = init.status;
    this.code = init.code;
    this.requestId = init.requestId ?? null;
    this.details = init.details ?? [];
    this.retryAfter = init.retryAfter ?? null;
  }

  /** A human-readable list of field validation problems, if any. */
  get fieldErrors(): string[] {
    return this.details
      .map((d) => {
        const loc = Array.isArray(d.loc) ? d.loc.filter((p) => p !== "body").join(".") : "";
        const msg = typeof d.msg === "string" ? d.msg : "";
        return msg ? (loc ? `${loc}: ${msg}` : msg) : "";
      })
      .filter(Boolean);
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

/** Turn a non-2xx response into an ApiError (reads the standard error envelope if present). */
export async function toApiError(response: Response): Promise<ApiError> {
  let body: Partial<ApiErrorBody> | null = null;
  try {
    body = (await response.json()) as Partial<ApiErrorBody>;
  } catch {
    body = null;
  }
  const retryHeader = response.headers.get("Retry-After");
  const retryAfter = retryHeader && /^\d+$/.test(retryHeader) ? Number(retryHeader) : null;
  const err = body?.error;
  return new ApiError({
    status: response.status,
    code: err?.code ?? `http_${response.status}`,
    message: err?.message ?? (response.statusText || `Request failed (${response.status})`),
    requestId: err?.request_id ?? response.headers.get("X-Request-ID"),
    details: err?.details,
    retryAfter,
  });
}

export function networkError(cause: unknown): ApiError {
  const aborted = cause instanceof DOMException && cause.name === "AbortError";
  return new ApiError({
    status: 0,
    code: aborted ? "aborted" : "network_error",
    message: aborted
      ? "The request was cancelled."
      : `Cannot reach the Atlas API at ${API_BASE_URL}. Check your connection and that the server is running.`,
  });
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  query?: Record<string, string | number | boolean | null | undefined>;
  signal?: AbortSignal;
  /** Send without the Authorization header (login/register). */
  anonymous?: boolean;
}

export function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const url = new URL(API_BASE_URL + path);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, String(value));
  }
  return url.toString();
}

export function authHeaders(anonymous = false): Record<string, string> {
  const token = tokenStore.get();
  return !anonymous && token ? { Authorization: `Bearer ${token}` } : {};
}

/** Call after a 401: forget the token and let the app redirect. */
export function handleUnauthorized(): void {
  tokenStore.set(null);
  unauthorizedHandler?.();
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, query, signal, anonymous = false } = options;
  const headers: Record<string, string> = { Accept: "application/json", ...authHeaders(anonymous) };
  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body; // the browser sets the multipart boundary
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }

  let response: Response;
  try {
    response = await fetch(buildUrl(path, query), { method, headers, body: payload, signal });
  } catch (cause) {
    throw networkError(cause);
  }

  if (!response.ok) {
    const error = await toApiError(response);
    if (response.status === 401 && !anonymous) handleUnauthorized();
    throw error;
  }
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}
