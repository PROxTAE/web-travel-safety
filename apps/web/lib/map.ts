import type { LngLatBoundsLike } from "maplibre-gl";

import type { MapMarker, MapRoute } from "@/components/map/MapView";
import type { AlertItem, DisasterEvent, RecommendationResponse, RouteCandidate, Severity, Trip } from "@/lib/api/generated/contracts";

export function boundsOf(coords: number[][]): LngLatBoundsLike | null {
  if (!coords.length) return null;
  let w = 180,
    s = 90,
    e = -180,
    n = -90;
  for (const [lon, lat] of coords) {
    if (lon === undefined || lat === undefined) continue;
    w = Math.min(w, lon);
    e = Math.max(e, lon);
    s = Math.min(s, lat);
    n = Math.max(n, lat);
  }
  return [
    [w, s],
    [e, n],
  ];
}

export function tripMarkers(trip: Trip): MapMarker[] {
  return [
    { id: "origin", kind: "origin", lon: trip.origin.coordinates.coordinates[0]!, lat: trip.origin.coordinates.coordinates[1]!, label: trip.origin.display_name },
    { id: "destination", kind: "destination", lon: trip.destination.coordinates.coordinates[0]!, lat: trip.destination.coordinates.coordinates[1]!, label: trip.destination.display_name },
  ];
}

export function routeToMapRoute(r: RouteCandidate, variant: MapRoute["variant"]): MapRoute {
  return { id: r.route_id, coordinates: r.geometry.coordinates, variant };
}

export function recommendationRoutes(rec: RecommendationResponse, selectedId?: string | null): MapRoute[] {
  const out: MapRoute[] = [];
  if (rec.primary_route) out.push(routeToMapRoute(rec.primary_route, rec.primary_route.route_id === selectedId ? "safer" : "recommended"));
  for (const alt of rec.alternatives ?? []) out.push(routeToMapRoute(alt, alt.route_id === selectedId ? "safer" : "alternative"));
  return out;
}

export function severityBucket(sev: Severity | null | undefined): MapMarker["severity"] {
  switch (sev) {
    case "SEVERE":
    case "EXTREME":
      return "HIGH";
    case "MODERATE":
      return "MODERATE";
    case "MINOR":
    case "INFO":
      return "LOW";
    default:
      return "UNKNOWN";
  }
}

/** Representative point of any GeoJSON geometry (centroid of the first ring for polygons). */
export function geometryPoint(g: DisasterEvent["geometry"]): [number, number] | null {
  if (g.type === "Point") return [g.coordinates[0]!, g.coordinates[1]!];
  const ring: number[][] | undefined =
    g.type === "LineString"
      ? (g.coordinates as number[][])
      : g.type === "Polygon"
        ? (g.coordinates as number[][][])[0]
        : (g.coordinates as number[][][][])[0]?.[0];
  if (!ring?.length) return null;
  let lon = 0;
  let lat = 0;
  for (const p of ring) {
    lon += p[0]!;
    lat += p[1]!;
  }
  return [lon / ring.length, lat / ring.length];
}

export function eventMarkers(events: DisasterEvent[]): MapMarker[] {
  const out: MapMarker[] = [];
  for (const e of events) {
    const pt = geometryPoint(e.geometry);
    if (!pt) continue;
    out.push({
      id: e.event_id,
      kind: "event",
      lon: pt[0],
      lat: pt[1],
      severity: severityBucket(e.severity),
      label: `${e.title} (${e.severity ?? "UNKNOWN"})`,
      data: e,
    });
  }
  return out;
}

export function eventPolygons(events: DisasterEvent[]) {
  const out: Array<{ id: string; coordinates: number[][][]; severity: MapMarker["severity"] }> = [];
  for (const e of events) {
    if (e.geometry.type === "Polygon") out.push({ id: e.event_id, coordinates: e.geometry.coordinates as number[][][], severity: severityBucket(e.severity) });
    if (e.geometry.type === "MultiPolygon")
      for (const [i, poly] of (e.geometry.coordinates as number[][][][]).entries()) out.push({ id: `${e.event_id}:${i}`, coordinates: poly, severity: severityBucket(e.severity) });
  }
  return out;
}

export function alertToMarker(a: AlertItem, lon: number, lat: number): MapMarker {
  return { id: a.alert_id, kind: "event", lon, lat, severity: severityBucket(a.severity), label: a.title, data: a };
}

export function bboxString(b: [number, number, number, number]): string {
  return b.map((v) => v.toFixed(3)).join(",");
}

/** Keeps viewport queries under the API's 10° limit by clamping around the centre. */
export function clampBbox(b: [number, number, number, number], maxDeg = 9.9): [number, number, number, number] {
  const [w, s, e, n] = b;
  const cx = (w + e) / 2;
  const cy = (s + n) / 2;
  const hw = Math.min((e - w) / 2, maxDeg / 2);
  const hh = Math.min((n - s) / 2, maxDeg / 2);
  return [Math.max(-180, cx - hw), Math.max(-90, cy - hh), Math.min(180, cx + hw), Math.min(90, cy + hh)];
}
