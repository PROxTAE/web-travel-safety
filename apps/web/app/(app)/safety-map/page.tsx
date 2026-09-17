"use client";

import { Button } from "@heroui/react";
import { Clock, CloudRain, Globe, Info, Layers, Mountain, Plus, TrainFront, X } from "lucide-react";
import Image from "next/image";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { DynamicMap } from "@/components/map/DynamicMap";
import type { MapMarker, Viewport } from "@/components/map/MapView";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataFreshness, ErrorState, SourceList } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";
import { useStartAssessment } from "@/features/assessment/hooks";
import { useSafetyEvents, type LayerKey, type TimeSlot } from "@/features/safety-map/hooks";
import { useCurrentTrip } from "@/features/trips/hooks";
import type { DisasterEvent, RecommendationResponse } from "@/lib/api/generated/contracts";
import { bboxString, clampBbox, eventMarkers, eventPolygons, recommendationRoutes } from "@/lib/map";
import { useRecommendation } from "@/features/trips/hooks";
import { cn } from "@/lib/utils";

const LAYERS: Array<{ key: LayerKey; label: string; icon: typeof CloudRain; apiLayer: "disasters" | "alerts" | null; types: string[] }> = [
  { key: "weather", label: "Weather", icon: CloudRain, apiLayer: "disasters", types: ["STORM", "CYCLONE", "FLOOD", "EXTREME_TEMPERATURE"] },
  { key: "transport", label: "Transport", icon: TrainFront, apiLayer: "alerts", types: ["TRANSPORT_CLOSURE"] },
  { key: "natural_hazards", label: "Natural hazards", icon: Mountain, apiLayer: "disasters", types: ["EARTHQUAKE", "WILDFIRE", "VOLCANO", "LANDSLIDE"] },
  { key: "health_safety", label: "Health & safety", icon: Plus, apiLayer: "disasters", types: ["HEALTH", "OTHER"] },
];
const SLOTS: Array<{ key: TimeSlot; label: string; lookbackDays: number }> = [
  { key: "now", label: "Now", lookbackDays: 3 },
  { key: "6h", label: "6 hr", lookbackDays: 7 },
  { key: "12h", label: "12 hr", lookbackDays: 14 },
];

/** Screen 03. Viewport/time/layers live in the URL so a view can be shared and reloaded. */
export default function SafetyMapPage() {
  const search = useSearchParams();
  const router = useRouter();
  const toast = useToast();
  const [viewport, setViewport] = useState<Viewport | null>(null);
  const [selected, setSelected] = useState<DisasterEvent | null>(null);
  const layers = useMemo<LayerKey[]>(() => {
    const raw = search.get("layers");
    return raw ? (raw.split(",").filter((l) => LAYERS.some((x) => x.key === l)) as LayerKey[]) : LAYERS.map((l) => l.key);
  }, [search]);
  const slot = (search.get("t") as TimeSlot | null) ?? "now";
  const slotMeta = SLOTS.find((s) => s.key === slot) ?? SLOTS[0]!;
  const debounced = useDebounced(viewport, 400);
  const bbox = debounced ? bboxString(clampBbox(debounced.bbox)) : null;
  const apiLayers = useMemo(() => Array.from(new Set(LAYERS.filter((l) => layers.includes(l.key)).map((l) => l.apiLayer).filter(Boolean))) as string[], [layers]);
  const events = useSafetyEvents(bbox, slotMeta.lookbackDays, apiLayers);

  const trips = useCurrentTrip();
  const rec = useRecommendation(trips.trip?.latest_recommendation_id);
  const routes = useMemo(() => (rec.data ? recommendationRoutes(rec.data) : []), [rec.data]);

  const disasters = useMemo(() => {
    const raw = (events.data?.data.layers.disasters ?? []) as DisasterEvent[];
    const allowed = new Set(LAYERS.filter((l) => layers.includes(l.key)).flatMap((l) => l.types));
    return raw.filter((e) => allowed.has(e.event_type));
  }, [events.data, layers]);
  const markers = useMemo(() => eventMarkers(disasters), [disasters]);
  const polygons = useMemo(() => eventPolygons(disasters), [disasters]);
  const degraded = events.data?.meta.degraded_services ?? [];

  const setParam = (key: string, value: string) => {
    const p = new URLSearchParams(search.toString());
    p.set(key, value);
    router.replace(`/safety-map?${p.toString()}`, { scroll: false });
  };
  const toggleLayer = (k: LayerKey) => {
    const next = layers.includes(k) ? layers.filter((x) => x !== k) : [...layers, k];
    setParam("layers", next.join(","));
  };
  const onViewport = useCallback((v: Viewport) => setViewport(v), []);
  const onMarker = useCallback((m: MapMarker) => setSelected((m.data as DisasterEvent) ?? null), []);

  return (
    <>
      <PageHeader
        title="Global Safety Map"
        subtitle="See real-time risks around the world and plan safer journeys"
        icon={<Globe aria-hidden />}
        aside={
          <ul className="sta-card flex items-center gap-4 px-4 py-2 text-sm font-semibold text-navy" aria-label="Legend">
            <li className="flex items-center gap-2"><span className="h-3 w-3 rounded-full bg-primary" aria-hidden />Low</li>
            <li className="flex items-center gap-2"><span className="h-3 w-3 rounded-full bg-amber" aria-hidden />Moderate</li>
            <li className="flex items-center gap-2"><span className="h-3 w-3 rounded-full bg-coral" aria-hidden />High</li>
            <li className="flex items-center gap-2"><span className="h-3 w-3 rounded-full bg-[#8b93a7]" aria-hidden />Unknown</li>
          </ul>
        }
      />
      <div className="sta-card relative p-2 min-h-[34rem] h-[60vh]">
        <DynamicMap ariaLabel="Global safety map" markers={markers} polygons={polygons} routes={routes} center={[20, 15]} zoom={1.6} onViewportChange={onViewport} onMarkerClick={onMarker} />

        {/* Risk layers panel */}
        <section className="absolute left-4 top-4 sta-card w-60 p-3" aria-labelledby="risk-layers">
          <h2 id="risk-layers" className="flex items-center gap-2 font-extrabold text-navy"><Layers className="text-primary-deep" aria-hidden />Risk Layers</h2>
          <ul className="mt-2 flex flex-col divide-y divide-line">
            {LAYERS.map((l) => {
              const Icon = l.icon;
              const on = layers.includes(l.key);
              return (
                <li key={l.key} className="flex items-center gap-2 py-2">
                  <span className="sta-icon-tile !w-8 !h-8 bg-surface-secondary text-navy"><Icon size={16} aria-hidden /></span>
                  <span className="flex-1 text-sm font-semibold text-navy">{l.label}</span>
                  <button type="button" role="switch" aria-checked={on} aria-label={l.label} onClick={() => toggleLayer(l.key)} className={cn("relative h-6 w-11 rounded-full transition-colors", on ? "bg-primary" : "bg-line")}>
                    <span className={cn("absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all", on ? "left-5.5" : "left-0.5")} />
                  </button>
                </li>
              );
            })}
          </ul>
        </section>

        {/* status */}
        <div className="absolute bottom-4 left-4 flex flex-col gap-1 text-xs">
          {events.isFetching && <span className="rounded-lg bg-white/95 px-2 py-1 text-ink-muted shadow-card">Updating…</span>}
          {events.isError && <ErrorState error={events.error} onRetry={() => events.refetch()} compact />}
          {!events.isError && events.data && (
            <span className="rounded-lg bg-white/95 px-2 py-1 text-ink-muted shadow-card">
              {markers.length} events in view · <DataFreshness fetchedAt={events.data.meta.generated_at} maxAgeMinutes={15} />
            </span>
          )}
          {degraded.length > 0 && <span className="rounded-lg bg-amber/15 px-2 py-1 text-[#b45f00] shadow-card">Limited: {degraded.join(", ")}</span>}
          {layers.length === 0 && <span className="rounded-lg bg-white/95 px-2 py-1 text-ink-muted shadow-card">Turn on layers to see more risk information.</span>}
        </div>

        {/* selected event card */}
        {selected && (
          <EventCard event={selected} onClose={() => setSelected(null)} trip={trips.trip} rec={rec.data} onAvoidStarted={(rid) => { toast.push({ tone: "success", title: "Reassessing your route", description: "We're avoiding this area." }); router.push(`/trips/${trips.trip!.id}?run=${rid}`); }} />
        )}
        <Image src="/assets/mascot/mascot-warning.png" alt="" width={140} height={140} className="pointer-events-none absolute bottom-3 right-3 hidden xl:block drop-shadow" />
      </div>

      {/* timeline */}
      <section className="sta-card mt-4 flex flex-wrap items-center gap-6 p-4" aria-labelledby="upcoming">
        <div className="flex items-center gap-3">
          <span className="sta-icon-tile bg-mint text-primary-deep"><Clock aria-hidden /></span>
          <div>
            <h2 id="upcoming" className="font-extrabold text-navy">Upcoming conditions</h2>
            <p className="text-sm text-ink-muted">See how risks may change along your route.</p>
          </div>
        </div>
        <div className="flex flex-1 items-center justify-center gap-8" role="radiogroup" aria-label="Time window">
          {SLOTS.map((s) => (
            <button key={s.key} type="button" role="radio" aria-checked={slot === s.key} onClick={() => setParam("t", s.key)} className={cn("flex flex-col items-center gap-1 text-sm font-semibold", slot === s.key ? "text-navy" : "text-ink-muted")}>
              {s.label}
              <span className={cn("h-3 w-3 rounded-full", s.key === "now" ? "bg-primary" : s.key === "6h" ? "bg-amber" : "bg-coral")} aria-hidden />
              <span className="text-[11px] font-normal">{s.lookbackDays}-day window</span>
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2 rounded-2xl bg-surface-secondary px-3 py-2 text-xs text-ink-muted"><Info size={14} aria-hidden />Forecast windows use the event validity times from official sources.</div>
      </section>
    </>
  );
}

function EventCard({
  event,
  onClose,
  trip,
  rec,
  onAvoidStarted,
}: {
  event: DisasterEvent;
  onClose: () => void;
  trip: { id: string } | null;
  rec: RecommendationResponse | null | undefined;
  onAvoidStarted: (requestId: string) => void;
}) {
  const start = useStartAssessment(trip?.id);
  const [details, setDetails] = useState(false);
  const sev = event.severity ?? "UNKNOWN";
  const tone = sev === "SEVERE" || sev === "EXTREME" ? "bg-coral/15 text-coral" : sev === "MODERATE" ? "bg-amber/15 text-[#b45f00]" : "bg-mint text-primary-deep";
  return (
    <aside className="absolute right-4 top-4 sta-card w-[min(22rem,calc(100%-2rem))] p-4" aria-label={event.title}>
      <div className="flex items-start gap-3">
        <span className={cn("sta-icon-tile", tone)}><CloudRain aria-hidden /></span>
        <div className="flex-1 min-w-0">
          <h3 className="font-extrabold text-navy truncate">{event.title}</h3>
          <span className={cn("mt-1 inline-block rounded-pill px-2 py-0.5 text-xs font-bold", tone)}>{sev.toLowerCase()} risk{event.official ? " · official" : ""}</span>
        </div>
        <button type="button" aria-label="Close" onClick={onClose} className="rounded-full p-1 hover:bg-mint"><X size={18} aria-hidden /></button>
      </div>
      {event.description && <p className={cn("mt-2 text-sm text-navy", !details && "line-clamp-2")}>{event.description}</p>}
      <p className="mt-2"><DataFreshness fetchedAt={event.source.fetched_at} label="Updated" maxAgeMinutes={180} /></p>
      <div className="mt-3 grid grid-cols-2 gap-2">
        <Button variant="outline" onPress={() => setDetails((d) => !d)}>{details ? "Hide details" : "View details"}</Button>
        <Button isDisabled={!trip || start.isPending} onPress={() => start.mutate({ question: `Avoid the area of ${event.title} (${event.event_id}). Suggest a safer route.`, intent_hint: "CHECK_SAFETY" }, { onSuccess: (r) => onAvoidStarted(r.request_id) })}>
          Avoid area
        </Button>
      </div>
      {details && (
        <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
          <div><dt className="text-ink-muted">Type</dt><dd className="font-semibold text-navy">{event.event_type}</dd></div>
          <div><dt className="text-ink-muted">Countries</dt><dd className="font-semibold text-navy">{event.country_codes?.join(", ") || "—"}</dd></div>
          <div><dt className="text-ink-muted">Effective</dt><dd className="font-semibold text-navy">{event.effective_at ? new Date(event.effective_at).toLocaleString() : "—"}</dd></div>
          <div><dt className="text-ink-muted">Ends</dt><dd className="font-semibold text-navy">{event.ends_at ? new Date(event.ends_at).toLocaleString() : "unknown"}</dd></div>
          {event.instruction && <div className="col-span-2"><dt className="text-ink-muted">Instruction</dt><dd className="text-navy">{event.instruction}</dd></div>}
          <div className="col-span-2"><SourceList sources={[event.source]} /></div>
        </dl>
      )}
      <p className="mt-3 flex items-center gap-2 rounded-2xl bg-surface-secondary p-2 text-xs text-ink-muted">
        <Info size={14} aria-hidden />
        {trip ? (rec ? "Our AI can suggest a safer route around this area." : "Run an assessment first to compare routes.") : "Plan a trip to get a safer route around this area."}
      </p>
    </aside>
  );
}

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  const t = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (t.current) clearTimeout(t.current);
    t.current = setTimeout(() => setV(value), ms);
    return () => {
      if (t.current) clearTimeout(t.current);
    };
  }, [value, ms]);
  return v;
}
