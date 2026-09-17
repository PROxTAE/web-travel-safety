import { getToken } from "next-auth/jwt";
import { NextResponse, type NextRequest } from "next/server";

/**
 * Next.js 16 proxy (formerly middleware): protects the app routes by checking the encrypted session cookie.
 * Unauthenticated visitors are sent to /login. The backend proxy route enforces its own token check as well,
 * so a stale cookie never reaches the API.
 */
export async function proxy(req: NextRequest): Promise<NextResponse> {
  const { pathname } = req.nextUrl;
  const isPublic =
    pathname === "/login" || pathname.startsWith("/api/auth") || pathname === "/api/health" || pathname.startsWith("/assets");
  if (isPublic) return NextResponse.next();
  const secret = process.env.AUTH_SECRET;
  if (!secret) return NextResponse.next(); // misconfiguration surfaces as a server error from the page itself
  const token = await getToken({ req, secret, secureCookie: (process.env.AUTH_URL ?? "").startsWith("https://") });
  if (!token || token.error === "RefreshTokenError") {
    if (pathname.startsWith("/api/")) {
      // XHR/EventSource callers get a stable 401 envelope instead of an HTML redirect
      return NextResponse.json(
        { error: { code: "AUTHENTICATION_REQUIRED", message: "sign in required", retryable: false }, meta: {} },
        { status: 401 },
      );
    }
    const url = new URL("/login", req.nextUrl.origin);
    if (pathname !== "/") url.searchParams.set("next", pathname);
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|assets/).*)"],
};
