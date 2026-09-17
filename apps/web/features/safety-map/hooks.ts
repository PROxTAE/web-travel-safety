"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { endpoints } from "@/lib/api/endpoints";
import { queryKeys } from "@/lib/query";

export type TimeSlot = "now" | "6h" | "12h";
export type LayerKey = "weather" | "transport" | "natural_hazards" | "health_safety";

/** Viewport query; TanStack cancels the previous request through the AbortSignal when the key changes. */
export function useSafetyEvents(bbox: string | null, lookbackDays: number, layers: string[]) {
  const layerKey = layers.join(",");
  return useQuery({
    queryKey: queryKeys.safety(bbox ?? "", lookbackDays, layerKey),
    queryFn: async ({ signal }) => endpoints.safetyEvents(bbox as string, lookbackDays, layerKey, signal),
    enabled: Boolean(bbox) && layers.length > 0,
    placeholderData: keepPreviousData,
    staleTime: 120_000,
  });
}
