import { accessTokenForProxy } from "@/lib/auth";
import { serverEnv } from "@/lib/env";

/**
 * Same-origin proxy to the public API. The browser only ever talks to this route with its HttpOnly session cookie;
 * the bearer token is attached here. Only /api/v1/* is reachable, and only a fixed header allowlist is forwarded.
 * SSE responses are streamed through untouched.
 */
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const FORWARD_REQUEST_HEADERS = ["content-type", "accept", "if-match", "idempotency-key", "last-event-id", "x-request-id"];
const FORWARD_RESPONSE_HEADERS = [
  "content-type",
  "etag",
  "retry-after",
  "x-request-id",
  "x-correlation-id",
  "cache-control",
  "x-accel-buffering",
];

function unauthorized(reason: string): Response {
  return Response.json(
    { error: { code: "AUTHENTICATION_REQUIRED", message: reason, retryable: false }, meta: {} },
    { status: 401 },
  );
}

async function proxy(request: Request, ctx: { params: Promise<{ path: string[] }> }): Promise<Response> {
  const { path } = await ctx.params;
  if (!path?.length || path.some((p) => p === ".." || p.includes("\\"))) {
    return Response.json({ error: { code: "NOT_FOUND", message: "unknown route" } }, { status: 404 });
  }
  const { token, error } = await accessTokenForProxy(request);
  if (!token) return unauthorized(error ?? "sign in required");

  const env = serverEnv();
  const incoming = new URL(request.url);
  const target = new URL(`/api/v1/${path.map(encodeURIComponent).join("/")}`, env.API_INTERNAL_BASE_URL);
  target.search = incoming.search;

  const headers = new Headers();
  for (const name of FORWARD_REQUEST_HEADERS) {
    const v = request.headers.get(name);
    if (v) headers.set(name, v);
  }
  headers.set("authorization", `Bearer ${token}`);
  headers.set("accept-language", request.headers.get("accept-language") ?? "en");
  const hasBody = !["GET", "HEAD"].includes(request.method);
  const isStream = request.headers.get("accept")?.includes("text/event-stream") ?? false;

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      redirect: "manual",
      cache: "no-store",
      signal: isStream ? request.signal : AbortSignal.any([request.signal, AbortSignal.timeout(20_000)]),
    });
  } catch (err) {
    const timeout = err instanceof Error && err.name === "TimeoutError";
    return Response.json(
      {
        error: {
          code: timeout ? "DEPENDENCY_TIMEOUT" : "DEPENDENCY_UNAVAILABLE",
          message: timeout ? "API timed out" : "API unreachable",
          retryable: true,
        },
        meta: {},
      },
      { status: timeout ? 504 : 503 },
    );
  }

  const out = new Headers();
  for (const name of FORWARD_RESPONSE_HEADERS) {
    const v = upstream.headers.get(name);
    if (v) out.set(name, v);
  }
  if (!out.has("cache-control")) out.set("cache-control", "no-store");
  return new Response(upstream.body, { status: upstream.status, headers: out });
}

export { proxy as GET, proxy as POST, proxy as PATCH, proxy as PUT, proxy as DELETE };
