"use client";

import { QueryClient } from "@tanstack/react-query";

import { ApiError } from "@/lib/api/client";

/** Retry only transient failures (429/5xx/network); never 4xx business errors. Stale times follow data freshness. */
export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        gcTime: 5 * 60_000,
        refetchOnWindowFocus: false,
        retry: (count, error) => count < 2 && error instanceof ApiError && error.retryable && !error.isAuth,
        retryDelay: (attempt, error) =>
          error instanceof ApiError && error.retryAfterSeconds ? error.retryAfterSeconds * 1000 : Math.min(1000 * 2 ** attempt, 8000),
      },
      mutations: { retry: false },
    },
  });
}

export const queryKeys = {
  me: ["me"] as const,
  consents: ["consents"] as const,
  emergencyProfile: ["emergency-profile"] as const,
  trips: ["trips"] as const,
  trip: (id: string) => ["trips", id] as const,
  run: (id: string) => ["runs", id] as const,
  recommendation: (id: string) => ["recommendations", id] as const,
  conversations: ["conversations"] as const,
  messages: (id: string) => ["conversations", id, "messages"] as const,
  safety: (bbox: string, lookback: number, layers: string) => ["safety", bbox, lookback, layers] as const,
  contacts: (cc: string, sub?: string) => ["emergency", "contacts", cc, sub ?? ""] as const,
  nearby: (type: string, lat: number, lon: number) => ["emergency", "nearby", type, lat, lon] as const,
};
