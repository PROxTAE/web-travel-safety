"use client";

import dynamic from "next/dynamic";

import type { MapViewProps } from "@/components/map/MapView";

/** MapLibre is loaded only in the browser and only when a page needs it (bundle budget). */
export const DynamicMap = dynamic<MapViewProps>(() => import("@/components/map/MapView"), {
  ssr: false,
  loading: () => <div className="h-full w-full rounded-2xl bg-[#dcecf6] animate-pulse" aria-busy="true" aria-label="Loading map" />,
});
