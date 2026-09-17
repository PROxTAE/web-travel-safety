import NextAuth, { type NextAuthConfig } from "next-auth";
import type { JWT } from "next-auth/jwt";
import Keycloak from "next-auth/providers/keycloak";

import { serverEnv } from "@/lib/env";

/**
 * Auth.js (OIDC Authorization Code + PKCE) against Keycloak. The access/refresh tokens live only inside the
 * encrypted HttpOnly session cookie; the browser never sees them. The backend proxy attaches the access token.
 */
declare module "next-auth" {
  interface Session {
    error?: "RefreshTokenError";
    user: { name?: string | null; email?: string | null; image?: string | null };
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    access_token?: string;
    refresh_token?: string;
    expires_at?: number; // epoch seconds
    id_token?: string;
    error?: "RefreshTokenError";
  }
}

function internalIssuer(): string {
  const env = serverEnv();
  return env.OIDC_INTERNAL_ISSUER ?? env.OIDC_ISSUER;
}

async function refreshAccessToken(token: JWT): Promise<JWT> {
  const env = serverEnv();
  if (!token.refresh_token) return { ...token, error: "RefreshTokenError" };
  const res = await fetch(`${internalIssuer()}/protocol/openid-connect/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "refresh_token",
      client_id: env.OIDC_CLIENT_ID,
      client_secret: env.OIDC_CLIENT_SECRET,
      refresh_token: token.refresh_token,
    }),
    signal: AbortSignal.timeout(8000),
  });
  if (!res.ok) return { ...token, error: "RefreshTokenError" };
  const data = (await res.json()) as {
    access_token: string;
    expires_in: number;
    refresh_token?: string;
    id_token?: string;
  };
  return {
    ...token,
    access_token: data.access_token,
    expires_at: Math.floor(Date.now() / 1000 + data.expires_in),
    refresh_token: data.refresh_token ?? token.refresh_token,
    id_token: data.id_token ?? token.id_token,
    error: undefined,
  };
}

export function buildAuthConfig(): NextAuthConfig {
  const env = serverEnv();
  return {
    secret: env.AUTH_SECRET,
    trustHost: true,
    session: { strategy: "jwt", maxAge: 8 * 60 * 60 },
    pages: { signIn: "/login" },
    providers: [
      Keycloak({
        // the browser is sent to the public issuer; server-to-server calls use the docker-network issuer.
        // Explicit endpoints skip discovery (which would hand back public URLs unreachable from the container).
        issuer: env.OIDC_ISSUER,
        clientId: env.OIDC_CLIENT_ID,
        clientSecret: env.OIDC_CLIENT_SECRET,
        authorization: {
          url: `${env.OIDC_ISSUER}/protocol/openid-connect/auth`,
          params: { scope: "openid profile email smart-travel-api" },
        },
        token: `${internalIssuer()}/protocol/openid-connect/token`,
        userinfo: `${internalIssuer()}/protocol/openid-connect/userinfo`,
        jwks_endpoint: `${internalIssuer()}/protocol/openid-connect/certs`,
      }),
    ],
    callbacks: {
      async jwt({ token, account }) {
        if (account) {
          return {
            ...token,
            access_token: account.access_token,
            refresh_token: account.refresh_token,
            id_token: account.id_token,
            expires_at: account.expires_at,
          };
        }
        if (token.expires_at && Date.now() / 1000 < token.expires_at - 30) return token;
        return refreshAccessToken(token);
      },
      async session({ session, token }) {
        // expose only display data + refresh error; never tokens
        session.error = token.error;
        session.user = { ...session.user, name: token.name ?? null, email: token.email ?? "", image: null };
        return session;
      },
    },
    events: {
      async signOut(message) {
        // best-effort RP-initiated logout so the Keycloak SSO session ends too
        const idToken = "token" in message ? message.token?.id_token : undefined;
        if (!idToken) return;
        try {
          await fetch(
            `${internalIssuer()}/protocol/openid-connect/logout?${new URLSearchParams({ id_token_hint: idToken })}`,
            { signal: AbortSignal.timeout(5000) },
          );
        } catch {
          /* provider unreachable: local session is already cleared */
        }
      },
    },
  };
}

export const { handlers, auth, signIn, signOut } = NextAuth(buildAuthConfig);

/** Server-only: the raw access token for the backend proxy (decoded from the HttpOnly session cookie). */
export async function accessTokenForProxy(request: Request): Promise<{ token: string | null; error?: string }> {
  const { getToken } = await import("next-auth/jwt");
  const env = serverEnv();
  const secure = (env.AUTH_URL ?? "").startsWith("https://");
  const token = await getToken({ req: request, secret: env.AUTH_SECRET, secureCookie: secure });
  if (!token) return { token: null, error: "no_session" };
  if (token.error) return { token: null, error: token.error };
  if (token.expires_at && Date.now() / 1000 > token.expires_at - 15) {
    const refreshed = await refreshAccessToken(token);
    return refreshed.error ? { token: null, error: refreshed.error } : { token: refreshed.access_token ?? null };
  }
  return { token: token.access_token ?? null };
}
