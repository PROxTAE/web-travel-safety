"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { endpoints, type TripInput } from "@/lib/api/endpoints";
import type { Trip } from "@/lib/api/generated/contracts";
import { queryKeys } from "@/lib/query";

export function useTrips() {
  return useQuery({ queryKey: queryKeys.trips, queryFn: async () => (await endpoints.trips()).data });
}

export function useTrip(id: string | null | undefined) {
  return useQuery({
    queryKey: queryKeys.trip(id ?? ""),
    queryFn: async () => (await endpoints.trip(id as string)).data,
    enabled: Boolean(id),
  });
}

/** The most recently updated active trip (dashboard "My Trip"). */
export function useCurrentTrip() {
  const q = useTrips();
  const trip = q.data?.find((t) => t.status === "ACTIVE") ?? q.data?.[0] ?? null;
  return { ...q, trip };
}

export function useCreateTrip() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: TripInput) => (await endpoints.createTrip(body)).data,
    onSuccess: (trip: Trip) => {
      qc.setQueryData(queryKeys.trip(trip.id), trip);
      void qc.invalidateQueries({ queryKey: queryKeys.trips });
    },
  });
}

export function usePatchTrip(tripId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (args: { revision: number; body: Parameters<typeof endpoints.patchTrip>[2] }) =>
      (await endpoints.patchTrip(tripId, args.revision, args.body)).data,
    onSuccess: (trip: Trip) => {
      qc.setQueryData(queryKeys.trip(trip.id), trip);
      void qc.invalidateQueries({ queryKey: queryKeys.trips });
    },
  });
}

export function useRecommendation(id: string | null | undefined) {
  return useQuery({
    queryKey: queryKeys.recommendation(id ?? ""),
    queryFn: async () => (await endpoints.recommendation(id as string)).data,
    enabled: Boolean(id),
    staleTime: 60_000,
  });
}
