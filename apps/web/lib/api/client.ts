/**
 * Browser API client. Calls go through the same-origin proxy (/api/backend/*) so no token ever lives in JS.
 * Every response is the shared envelope { data, meta } | { error, meta }; errors map to a stable ApiError.
 */
import type { components } from "@/lib/api/generated/public-api";

export type Envelope<T> = { data: T; meta: Meta };
export type Meta = {
  request_id?: string;
  correlation_id?: string;
  contract_version?: string;
  generated_at?: string;
  degraded_services?: string[];
};
export type FieldError = { path: string; code: string; message?: string | null };
export type Schemas = components["schemas"];

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly retryable: boolean;
  readonly retryAfterSeconds: number | null;
  readonly fieldErrors: FieldError[];
  readonly requestId: string | null;

  constructor(init: {
    code: string;
    message: string;
    status: number;
    retryable?: boolean;
    retryAfterSeconds?: number | null;
    fieldErrors?: FieldError[];
    requestId?: string | null;
  }) {
    super(init.message);
    this.name = "ApiError";
    this.code = init.code;
    this.status = init.status;
    this.retryable = init.retryable ?? false;
    this.retryAfterSeconds = init.retryAfterSeconds ?? null;
    this.fieldErrors = init.fieldErrors ?? [];
    this.requestId = init.requestId ?? null;
  }

  get isAuth(): boolean {
    return this.status === 401;
  }
}

export type RequestOptions = {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined | null>;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  timeoutMs?: number;
};

export type ApiResult<T> = { data: T; meta: Meta; etag: string | null; status: number };

export const BACKEND_PREFIX = "/api/backend";

export function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const clean = path.replace(/^\/+/, "");
  const url = new URL(`${BACKEND_PREFIX}/${clean}`, typeof window === "undefined" ? "http://localhost" : window.location.origin);
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
  }
  return typeof window === "undefined" ? url.pathname + url.search : url.pathname + url.search;
}

export function newCorrelationId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
}

/** Maps a non-2xx envelope (or a transport failure) to ApiError. Exported for tests. */
export function toApiError(status: number, payload: unknown, headers?: Headers): ApiError {
  const err = (payload as { error?: Record<string, unknown> } | null)?.error;
  const meta = (payload as { meta?: Meta } | null)?.meta;
  const retryAfterHeader = headers?.get("retry-after");
  const code = typeof err?.code === "string" ? err.code : status === 401 ? "AUTHENTICATION_REQUIRED" : "INTERNAL_ERROR";
  return new ApiError({
    code,
    message: typeof err?.message === "string" ? err.message : `request failed (${status})`,
    status,
    retryable: typeof err?.retryable === "boolean" ? err.retryable : [429, 502, 503, 504].includes(status),
    retryAfterSeconds:
      typeof err?.retry_after_seconds === "number"
        ? err.retry_after_seconds
        : retryAfterHeader && /^\d+$/.test(retryAfterHeader)
          ? Number(retryAfterHeader)
          : null,
    fieldErrors: Array.isArray(err?.field_errors) ? (err.field_errors as FieldError[]) : [],
    requestId: meta?.request_id ?? null,
  });
}

export async function apiRequest<T>(path: string, opts: RequestOptions = {}): Promise<ApiResult<T>> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(new DOMException("timeout", "TimeoutError")), opts.timeoutMs ?? 15_000);
  const onAbort = () => controller.abort(opts.signal?.reason);
  opts.signal?.addEventListener("abort", onAbort, { once: true });
  const headers: Record<string, string> = {
    accept: "application/json",
    "x-request-id": newCorrelationId(),
    ...opts.headers,
  };
  if (opts.body !== undefined) headers["content-type"] = "application/json";
  let res: Response;
  try {
    res = await fetch(buildUrl(path, opts.query), {
      method: opts.method ?? "GET",
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      signal: controller.signal,
      credentials: "same-origin",
    });
  } catch (e) {
    if (opts.signal?.aborted) throw e;
    const timedOut = e instanceof DOMException && e.name === "TimeoutError";
    throw new ApiError({
      code: timedOut ? "DEPENDENCY_TIMEOUT" : "NETWORK_ERROR",
      message: timedOut ? "The request timed out" : "You appear to be offline",
      status: 0,
      retryable: true,
    });
  } finally {
    clearTimeout(timeout);
    opts.signal?.removeEventListener("abort", onAbort);
  }
  if (res.status === 204) return { data: undefined as T, meta: {}, etag: res.headers.get("etag"), status: 204 };
  let payload: unknown = null;
  const text = await res.text();
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }
  if (!res.ok) throw toApiError(res.status, payload, res.headers);
  const env = payload as Envelope<T> | null;
  if (!env || !("data" in env)) {
    throw new ApiError({ code: "INTERNAL_ERROR", message: "malformed response envelope", status: res.status });
  }
  return { data: env.data, meta: env.meta ?? {}, etag: res.headers.get("etag"), status: res.status };
}

export const api = {
  get: <T>(path: string, opts?: Omit<RequestOptions, "method" | "body">) => apiRequest<T>(path, { ...opts, method: "GET" }),
  post: <T>(path: string, body?: unknown, opts?: Omit<RequestOptions, "method" | "body">) =>
    apiRequest<T>(path, { ...opts, method: "POST", body }),
  patch: <T>(path: string, body: unknown, opts?: Omit<RequestOptions, "method" | "body">) =>
    apiRequest<T>(path, { ...opts, method: "PATCH", body }),
  put: <T>(path: string, body: unknown, opts?: Omit<RequestOptions, "method" | "body">) =>
    apiRequest<T>(path, { ...opts, method: "PUT", body }),
  delete: <T>(path: string, opts?: Omit<RequestOptions, "method" | "body">) => apiRequest<T>(path, { ...opts, method: "DELETE" }),
};
