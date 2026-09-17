"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api/client";
import { endpoints } from "@/lib/api/endpoints";
import type { ConsentType, EmergencyProfile } from "@/lib/api/generated/contracts";
import { queryKeys } from "@/lib/query";

export type NearbyType = "POLICE" | "MEDICAL" | "EMBASSY" | "FIRE";

export function useEmergencyContacts(countryCode: string | null, subdivision?: string) {
  return useQuery({
    queryKey: queryKeys.contacts(countryCode ?? "", subdivision),
    queryFn: async () => (await endpoints.emergencyContacts(countryCode as string, subdivision)).data,
    enabled: Boolean(countryCode),
    staleTime: 10 * 60_000,
  });
}

export function useNearby(type: NearbyType | null, coords: { lat: number; lon: number } | null) {
  return useQuery({
    queryKey: queryKeys.nearby(type ?? "", coords?.lat ?? 0, coords?.lon ?? 0),
    queryFn: async () => (await endpoints.emergencyNearby(type as NearbyType, coords!.lat, coords!.lon)).data,
    enabled: Boolean(type && coords),
    retry: false,
  });
}

export function useConsents() {
  return useQuery({ queryKey: queryKeys.consents, queryFn: async () => (await endpoints.consents()).data });
}

export function useGrantConsent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (args: { type: ConsentType; granted: boolean }) =>
      (await endpoints.grantConsent(args.type, args.granted)).data,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.consents });
      void qc.invalidateQueries({ queryKey: queryKeys.me });
    },
  });
}

export function useEmergencyProfile() {
  return useQuery({
    queryKey: queryKeys.emergencyProfile,
    queryFn: async () => {
      try {
        return (await endpoints.emergencyProfile()).data;
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },
    retry: false,
  });
}

export function useSaveEmergencyProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: Omit<EmergencyProfile, "updated_at">) => (await endpoints.putEmergencyProfile(body)).data,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.emergencyProfile });
      void qc.invalidateQueries({ queryKey: queryKeys.me });
    },
  });
}

export type GeoState =
  | { status: "idle" }
  | { status: "requesting" }
  | { status: "denied" }
  | { status: "unavailable" }
  | { status: "ready"; lat: number; lon: number; accuracy: number; at: number };

/** Browser geolocation with explicit start/stop; nothing is requested until the user asks. */
export function useGeolocation(watch = false) {
  const [state, setState] = useState<GeoState>({ status: "idle" });
  const watchId = useRef<number | null>(null);

  const stop = useCallback(() => {
    if (watchId.current !== null && typeof navigator !== "undefined") navigator.geolocation.clearWatch(watchId.current);
    watchId.current = null;
    setState({ status: "idle" });
  }, []);

  const start = useCallback(() => {
    if (typeof navigator === "undefined" || !navigator.geolocation) {
      setState({ status: "unavailable" });
      return;
    }
    setState({ status: "requesting" });
    const ok = (p: GeolocationPosition) =>
      setState({
        status: "ready",
        lat: p.coords.latitude,
        lon: p.coords.longitude,
        accuracy: p.coords.accuracy,
        at: Date.now(),
      });
    const fail = (e: GeolocationPositionError) =>
      setState({ status: e.code === e.PERMISSION_DENIED ? "denied" : "unavailable" });
    const opts = { enableHighAccuracy: true, timeout: 15_000, maximumAge: 30_000 };
    if (watch) watchId.current = navigator.geolocation.watchPosition(ok, fail, opts);
    else navigator.geolocation.getCurrentPosition(ok, fail, opts);
  }, [watch]);

  useEffect(() => () => stop(), [stop]);
  return { state, start, stop };
}
