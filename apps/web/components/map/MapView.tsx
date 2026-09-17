"use client";

import * as maplibregl from "maplibre-gl";
import type { ErrorEvent, GeoJSONSource, LngLatBoundsLike, Map as MLMap, Marker, StyleSpecification } from "maplibre-gl";
import type { FeatureCollection } from "geojson";
import { useEffect, useRef, useState } from "react";

import { publicEnv } from "@/lib/env";

export type MapMarker = {
  id: string;
  lon: number;
  lat: number;
  kind: "origin" | "destination" | "event" | "poi" | "me";
  severity?: "LOW" | "MODERATE" | "HIGH" | "UNKNOWN";
  label?: string;
  data?: unknown;
};

export type MapRoute = {
  id: string;
  coordinates: number[][];
  variant: "recommended" | "original" | "alternative" | "safer";
};

export type Viewport = { bbox: [number, number, number, number]; zoom: number };

export type MapViewProps = {
  routes?: MapRoute[];
  markers?: MapMarker[];
  polygons?: Array<{ id: string; coordinates: number[][][]; severity?: MapMarker["severity"] }>;
  fitTo?: LngLatBoundsLike | null;
  center?: [number, number];
  zoom?: number;
  interactive?: boolean;
  onMarkerClick?: (m: MapMarker) => void;
  onViewportChange?: (v: Viewport) => void;
  className?: string;
  ariaLabel: string;
};

const ROUTE_STYLE: Record<MapRoute["variant"], { color: string; dash?: number[]; width: number }> = {
  recommended: { color: "#08B88A", dash: [1.5, 1.5], width: 5 },
  safer: { color: "#08B88A", width: 6 },
  original: { color: "#8b93a7", dash: [1, 2], width: 4 },
  alternative: { color: "#8b93a7", dash: [1, 2], width: 4 },
};

const SEVERITY_COLOR = { LOW: "#08B88A", MODERATE: "#FF9D1F", HIGH: "#F24E54", UNKNOWN: "#8b93a7" };

function fallbackStyle(): StyleSpecification {
  // Used only when the configured style URL fails to load: a plain canvas with attribution, never fake data.
  return { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": "#dcecf6" } }] };
}

/**
 * Thin MapLibre wrapper. Data (routes/markers/polygons) is drawn as live layers from props; nothing is computed here.
 * Loaded client-side only (dynamic import in callers) to keep it out of the server bundle.
 */
export function MapView({
  routes = [],
  markers = [],
  polygons = [],
  fitTo,
  center = [100.5, 13.75],
  zoom = 5,
  interactive = true,
  onMarkerClick,
  onViewportChange,
  className,
  ariaLabel,
}: MapViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const markerObjs = useRef<Marker[]>([]);
  const [ready, setReady] = useState(false);
  const [styleFailed, setStyleFailed] = useState(false);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: publicEnv.NEXT_PUBLIC_MAP_STYLE_URL,
      center,
      zoom,
      interactive,
      attributionControl: { compact: true },
      cooperativeGestures: false,
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-left");
    map.addControl(new maplibregl.GeolocateControl({ trackUserLocation: false }), "top-left");
    map.on("error", (e: ErrorEvent) => {
      if (!map.isStyleLoaded() && !styleFailed) {
        setStyleFailed(true);
        map.setStyle(fallbackStyle());
      }
      console.warn("map_error", e.error?.message ?? "unknown");
    });
    map.on("load", () => setReady(true));
    const emit = () => {
      const b = map.getBounds();
      onViewportChange?.({ bbox: [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()], zoom: map.getZoom() });
    };
    map.on("moveend", emit);
    map.once("load", emit);
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- map is created once; props are applied in effects below
  }, []);

  // routes + polygons as GeoJSON sources/layers
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const ensure = (id: string, data: FeatureCollection) => {
      const src = map.getSource(id) as GeoJSONSource | undefined;
      if (src) src.setData(data);
      else map.addSource(id, { type: "geojson", data });
    };
    ensure("sta-polygons", {
      type: "FeatureCollection",
      features: polygons.map((p) => ({
        type: "Feature",
        properties: { color: SEVERITY_COLOR[p.severity ?? "UNKNOWN"] },
        geometry: { type: "Polygon", coordinates: p.coordinates },
      })),
    });
    if (!map.getLayer("sta-polygons-fill")) {
      map.addLayer({
        id: "sta-polygons-fill",
        type: "fill",
        source: "sta-polygons",
        paint: { "fill-color": ["get", "color"], "fill-opacity": 0.22 },
      });
    }
    ensure("sta-routes", {
      type: "FeatureCollection",
      features: routes.map((r) => ({
        type: "Feature",
        properties: { variant: r.variant, ...ROUTE_STYLE[r.variant] },
        geometry: { type: "LineString", coordinates: r.coordinates },
      })),
    });
    if (!map.getLayer("sta-routes-line")) {
      map.addLayer({
        id: "sta-routes-line",
        type: "line",
        source: "sta-routes",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": ["get", "color"],
          "line-width": ["get", "width"],
          "line-dasharray": ["case", ["==", ["get", "variant"], "safer"], ["literal", [1, 0]], ["literal", [1.5, 1.5]]],
        },
      });
    }
  }, [routes, polygons, ready]);

  // markers as DOM elements (accessible buttons)
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    for (const m of markerObjs.current) m.remove();
    markerObjs.current = markers.map((m) => {
      const el = document.createElement("button");
      el.type = "button";
      el.className = "sta-marker";
      el.setAttribute("aria-label", m.label ?? m.kind);
      el.dataset.kind = m.kind;
      const color = m.kind === "origin" ? "#08B88A" : m.kind === "destination" ? "#F24E54" : m.kind === "me" ? "#2F86F6" : SEVERITY_COLOR[m.severity ?? "UNKNOWN"];
      const shape = m.kind === "event" ? "polygon(50% 0, 100% 100%, 0 100%)" : m.kind === "poi" ? "inset(0 round 4px)" : "circle(50%)";
      el.style.cssText = `width:${m.kind === "event" ? 22 : 18}px;height:${m.kind === "event" ? 22 : 18}px;background:${color};clip-path:${shape};border:0;cursor:pointer;box-shadow:0 0 0 4px ${color}33;`;
      el.addEventListener("click", () => onMarkerClick?.(m));
      return new maplibregl.Marker({ element: el }).setLngLat([m.lon, m.lat]).addTo(map);
    });
  }, [markers, ready, onMarkerClick]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !fitTo) return;
    map.fitBounds(fitTo, { padding: 48, maxZoom: 12, duration: 600 });
  }, [fitTo, ready]);

  return (
    <div className={className ?? "h-full w-full"} role="region" aria-label={ariaLabel}>
      <div ref={containerRef} className="h-full w-full rounded-2xl overflow-hidden" />
      {styleFailed && (
        <p role="status" className="absolute bottom-2 left-2 rounded-lg bg-white/90 px-2 py-1 text-xs text-[#b45f00]">
          Map tiles unavailable — routes and markers still shown on a plain canvas.
        </p>
      )}
    </div>
  );
}

export default MapView;
