import { z } from "zod";

/**
 * Runtime env validation. Only NEXT_PUBLIC_* values reach the browser; secrets stay server-side.
 * Fails fast at startup instead of rendering a half-configured app.
 */
const serverSchema = z.object({
  API_INTERNAL_BASE_URL: z.string().url().default("http://api:8000"),
  AUTH_SECRET: z.string().min(16),
  AUTH_URL: z.string().url().optional(),
  OIDC_ISSUER: z.string().url(),
  // issuer as reachable from the web container (docker network); defaults to OIDC_ISSUER
  OIDC_INTERNAL_ISSUER: z.string().url().optional(),
  OIDC_CLIENT_ID: z.string().min(1),
  OIDC_CLIENT_SECRET: z.string().min(1),
  APP_ENV: z.enum(["development", "test", "staging", "production"]).default("development"),
});

const publicSchema = z.object({
  NEXT_PUBLIC_API_BASE_URL: z.string().url().default("http://localhost:8000"),
  NEXT_PUBLIC_MAP_STYLE_URL: z.string().url().default("https://tiles.openfreemap.org/styles/liberty"),
});

export type ServerEnv = z.infer<typeof serverSchema>;
export type PublicEnv = z.infer<typeof publicSchema>;

let cachedServer: ServerEnv | undefined;

export function serverEnv(): ServerEnv {
  if (!cachedServer) {
    const parsed = serverSchema.safeParse(process.env);
    if (!parsed.success) {
      const missing = parsed.error.issues.map((i) => i.path.join(".")).join(", ");
      throw new Error(`web: invalid server environment (${missing})`);
    }
    cachedServer = parsed.data;
  }
  return cachedServer;
}

// Public values are inlined at build time by Next; keep the object literal so the bundler can see the keys.
export const publicEnv: PublicEnv = publicSchema.parse({
  NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL,
  NEXT_PUBLIC_MAP_STYLE_URL: process.env.NEXT_PUBLIC_MAP_STYLE_URL,
});

export function assertNoSecretLeak(): void {
  for (const key of Object.keys(process.env)) {
    if (key.startsWith("NEXT_PUBLIC_") && /SECRET|TOKEN|PASSWORD|KEY$/.test(key)) {
      throw new Error(`web: ${key} would expose a secret to the browser`);
    }
  }
}
