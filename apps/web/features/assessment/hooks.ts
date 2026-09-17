"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";

import { endpoints } from "@/lib/api/endpoints";
import type { Intent } from "@/lib/api/generated/contracts";
import { queryKeys } from "@/lib/query";
import { newIdempotencyKey } from "@/lib/utils";

const ACTIVE = new Set(["CREATED", "QUEUED", "RUNNING"]);

/**
 * Starts an assessment with a per-submission Idempotency-Key: a double click or a retry after a network hiccup
 * replays the same run instead of creating a duplicate. A new key is issued only after success.
 */
export function useStartAssessment(defaultTripId: string | null | undefined) {
  const qc = useQueryClient();
  const keyRef = useRef<string | null>(null);
  return useMutation({
    // the trip id can be passed per call (a trip created moments ago is not yet in the hook's closure)
    mutationFn: async (args: { tripId?: string; question?: string; intent_hint?: Intent } = {}) => {
      const tripId = args.tripId ?? defaultTripId;
      if (!tripId) throw new Error("no trip");
      keyRef.current ??= newIdempotencyKey();
      const body = { question: args.question, intent_hint: args.intent_hint };
      return (await endpoints.createAssessment(tripId, keyRef.current, body)).data;
    },
    onSuccess: (_ref, args) => {
      keyRef.current = null;
      const tripId = args.tripId ?? defaultTripId;
      if (tripId) void qc.invalidateQueries({ queryKey: queryKeys.trip(tripId) });
    },
  });
}

export function useRun(requestId: string | null | undefined, pollWhileActive = true) {
  return useQuery({
    queryKey: queryKeys.run(requestId ?? ""),
    queryFn: async () => (await endpoints.run(requestId as string)).data,
    enabled: Boolean(requestId),
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      return pollWhileActive && s && ACTIVE.has(s) ? 2000 : false;
    },
  });
}

export function useCancelRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => (await endpoints.cancelRun(id)).data,
    onSuccess: (run) => qc.setQueryData(queryKeys.run(run.request_id), run),
  });
}

export function useResumeRun() {
  const keyRef = useRef<string | null>(null);
  return useMutation({
    mutationFn: async (id: string) => {
      keyRef.current ??= newIdempotencyKey();
      return (await endpoints.resumeRun(id, keyRef.current)).data;
    },
    onSuccess: () => {
      keyRef.current = null;
    },
  });
}

export function useApplyRoute(tripId: string) {
  const qc = useQueryClient();
  const keyRef = useRef<string | null>(null);
  return useMutation({
    mutationFn: async (args: { revision: number; route_id: string; recommendation_id: string; acknowledge_risk?: boolean }) => {
      keyRef.current ??= newIdempotencyKey();
      const { revision, ...body } = args;
      return (await endpoints.applyRoute(tripId, revision, keyRef.current, body)).data;
    },
    onSuccess: (res) => {
      keyRef.current = null;
      qc.setQueryData(queryKeys.trip(tripId), res.trip);
      void qc.invalidateQueries({ queryKey: queryKeys.trips });
    },
  });
}
