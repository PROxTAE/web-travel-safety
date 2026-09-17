"use client";

import { Bot, CalendarDays, ChevronRight, CloudRain, MapPin, Send, ShieldCheck, TrainFront } from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { DetailSheet } from "@/components/dashboard/DetailSheet";
import { EmergencyQuickCard } from "@/components/dashboard/EmergencyQuickCard";
import { SummaryCards, transportHeadline, weatherHeadline } from "@/components/dashboard/SummaryCards";
import { DynamicMap } from "@/components/map/DynamicMap";
import type { MapMarker } from "@/components/map/MapView";
import { RecommendationCard } from "@/components/recommendation/RecommendationCard";
import { ButtonLink, DataFreshness, EmptyState, ErrorState, RiskBadge, SectionTitle, SourceList } from "@/components/ui/primitives";
import { useCreateConversation } from "@/features/conversations/hooks";
import { useCurrentTrip, useRecommendation } from "@/features/trips/hooks";
import { boundsOf, recommendationRoutes, tripMarkers } from "@/lib/map";
import { MODE_LABELS, formatDate, formatDuration, formatTime } from "@/lib/utils";

export default function DashboardPage() {
  const trips = useCurrentTrip();
  const trip = trips.trip;
  const rec = useRecommendation(trip?.latest_recommendation_id);
  const [sheet, setSheet] = useState<"weather" | "transport" | "risk" | null>(null);
  const [marker, setMarker] = useState<MapMarker | null>(null);
  const router = useRouter();
  const createConversation = useCreateConversation();
  const [question, setQuestion] = useState("");

  const routes = useMemo(() => (rec.data ? recommendationRoutes(rec.data, trip?.selected_route_id) : []), [rec.data, trip?.selected_route_id]);
  const markers = useMemo(() => {
    const out: MapMarker[] = trip ? tripMarkers(trip) : [];
    for (const a of rec.data?.alerts ?? []) {
      // alerts carry no coordinates in the public contract; pin them at the destination as "along your route"
      if (trip) out.push({ id: a.alert_id, kind: "event", lon: trip.destination.coordinates.coordinates[0]!, lat: trip.destination.coordinates.coordinates[1]!, severity: a.severity === "SEVERE" || a.severity === "EXTREME" ? "HIGH" : a.severity === "MODERATE" ? "MODERATE" : "LOW", label: a.title, data: a });
    }
    return out;
  }, [trip, rec.data]);
  const fit = useMemo(() => boundsOf(routes.flatMap((r) => r.coordinates).concat(markers.map((m) => [m.lon, m.lat]))), [routes, markers]);

  const askAssistant = () => {
    createConversation.mutate(
      { trip_id: trip?.id ?? null },
      { onSuccess: (c) => router.push(`/assistant/${c.id}${question ? `?q=${encodeURIComponent(question)}` : ""}`) },
    );
  };

  if (trips.isError) return <ErrorState error={trips.error} onRetry={() => trips.refetch()} />;

  return (
    <div className="grid gap-4 xl:grid-cols-[1fr_1.35fr_1fr]">
      {/* Row 1: summary cards span two columns + hero banner */}
      <div className="xl:col-span-2">
        <SummaryCards rec={rec.data} loading={trips.isLoading || rec.isLoading} onOpen={setSheet} />
      </div>
      <div className="hidden xl:block relative rounded-card overflow-hidden min-h-28">
        <Image src="/assets/illustrations/hero-global-travel-banner.png" alt="" fill className="object-cover" sizes="400px" />
        <p className="absolute right-4 top-3 text-primary-deep italic font-semibold text-sm leading-tight text-right" aria-hidden>
          Beautiful Journeys
          <br />
          Safer Tomorrows
        </p>
      </div>

      {/* Column 1: My Trip */}
      <section className="sta-card p-4" aria-labelledby="my-trip">
        <SectionTitle icon={<CalendarDays aria-hidden />} aside={trip && <Link href={`/trips/${trip.id}`} className="text-sm font-semibold text-primary-deep">Edit</Link>}>
          <span id="my-trip">My Trip</span>
        </SectionTitle>
        {trips.isLoading ? (
          <div className="h-48 animate-pulse rounded-2xl bg-surface-secondary" />
        ) : !trip ? (
          <EmptyState title="No trip yet" description="Plan a trip to see live weather, transport and risk." action={<ButtonLink href="/trips/new">Plan a trip</ButtonLink>} />
        ) : (
          <>
            <div className="rounded-2xl bg-surface-secondary p-3">
              <p className="text-xl font-extrabold text-navy">
                {trip.origin.display_name} <span className="text-primary">→</span> {trip.destination.display_name}
              </p>
              <p className="text-sm text-ink-muted">
                {formatDate(trip.departure_time, trip.timezone)} · {formatTime(trip.departure_time, trip.timezone)}
              </p>
            </div>
            <ol className="mt-3 flex flex-col gap-2 border-l-2 border-dashed border-line pl-4">
              <li>
                <p className="font-bold text-navy flex items-center gap-2"><MapPin size={14} className="text-primary" aria-hidden />{trip.origin.display_name}</p>
                <p className="text-sm text-ink-muted">Depart {formatTime(trip.departure_time, trip.timezone)}</p>
              </li>
              <li>
                <p className="font-bold text-navy flex items-center gap-2"><MapPin size={14} className="text-coral" aria-hidden />{trip.destination.display_name}</p>
                <p className="text-sm text-ink-muted">
                  Arrive (est.){" "}
                  {rec.data?.primary_route
                    ? formatTime(new Date(new Date(trip.departure_time).getTime() + rec.data.primary_route.duration_seconds * 1000).toISOString(), trip.timezone)
                    : "—"}
                </p>
              </li>
            </ol>
            <div className="mt-3 grid gap-2">
              <button type="button" onClick={() => setSheet("transport")} className="flex items-center gap-3 rounded-2xl bg-surface-secondary p-3 text-left hover:bg-mint">
                <span className="sta-icon-tile !w-10 !h-10 bg-mint text-primary-deep"><TrainFront size={18} aria-hidden /></span>
                <span className="flex-1">
                  <span className="block text-xs text-ink-muted">Transport</span>
                  <span className="block font-bold text-navy">{trip.travel_modes.map((m) => MODE_LABELS[m]).join(" / ")}</span>
                  <span className="block text-xs text-ink-muted">{rec.data?.primary_route ? `Approx. ${formatDuration(rec.data.primary_route.duration_seconds)}` : "Duration after assessment"}</span>
                </span>
                <ChevronRight className="text-ink-muted" aria-hidden />
              </button>
              <button type="button" onClick={() => setSheet("weather")} className="flex items-center gap-3 rounded-2xl bg-surface-secondary p-3 text-left hover:bg-mint">
                <span className="sta-icon-tile !w-10 !h-10 bg-weather/10 text-weather"><CloudRain size={18} aria-hidden /></span>
                <span className="flex-1">
                  <span className="block text-xs text-ink-muted">Weather ({trip.destination.display_name})</span>
                  <span className="block font-bold text-weather">{weatherHeadline(rec.data).value}</span>
                  <span className="block text-xs text-ink-muted">{weatherHeadline(rec.data).detail}</span>
                </span>
                <ChevronRight className="text-ink-muted" aria-hidden />
              </button>
            </div>
            {!trip.latest_recommendation_id && (
              <ButtonLink href={`/trips/${trip.id}`} fullWidth className="mt-3">Find safe routes</ButtonLink>
            )}
          </>
        )}
        <Image src="/assets/illustrations/global-map-background.png" alt="" width={600} height={220} className="mt-4 rounded-2xl object-cover h-28 w-full" />
      </section>

      {/* Column 2: Route & Risk map */}
      <section className="sta-card p-4 flex flex-col min-h-[28rem]" aria-labelledby="route-map">
        <SectionTitle icon={<MapPin aria-hidden />} aside={<Link href="/safety-map" className="text-sm font-semibold text-primary-deep">Show risk layers</Link>}>
          <span id="route-map">Route &amp; Risk Map</span>
        </SectionTitle>
        <div className="relative flex-1 min-h-80">
          <DynamicMap ariaLabel="Route and risk map" routes={routes} markers={markers} fitTo={fit} onMarkerClick={setMarker} />
          {rec.data && (
            <Link
              href={`/trips/${trip!.id}/compare?recommendation=${rec.data.recommendation_id}`}
              className="absolute bottom-3 left-3 flex items-center gap-3 rounded-2xl bg-white/95 p-3 shadow-card hover:bg-white"
            >
              <span className="sta-icon-tile !w-10 !h-10 bg-mint text-primary-deep"><ShieldCheck size={18} aria-hidden /></span>
              <span>
                <span className="block font-bold text-navy">{rec.data.alternatives?.length ? "Safer route found" : "Route assessed"}</span>
                <span className="block text-xs text-ink-muted"><RiskBadge level={rec.data.risk_level} size="sm" /></span>
              </span>
              <ChevronRight className="text-ink-muted" aria-hidden />
            </Link>
          )}
          <div className="absolute bottom-3 right-3 rounded-xl bg-white/95 px-3 py-2 text-[11px] text-ink-muted shadow-card">
            <p><span className="inline-block w-6 border-t-4 border-dotted border-primary align-middle mr-1" />Recommended route</p>
            <p><span className="inline-block w-6 border-t-4 border-dotted border-[#8b93a7] align-middle mr-1" />Alternative route</p>
          </div>
        </div>
      </section>

      {/* Column 3: recommendation, emergency, assistant */}
      <div className="flex flex-col gap-4">
        {rec.isError ? (
          <ErrorState error={rec.error} onRetry={() => rec.refetch()} compact />
        ) : rec.data && trip ? (
          <RecommendationCard rec={rec.data} tripId={trip.id} compact />
        ) : (
          <section className="sta-card p-4">
            <SectionTitle>AI Recommendation</SectionTitle>
            <p className="text-sm text-ink-muted">{trip ? "Run an assessment to get a recommendation for this trip." : "No recommendation yet."}</p>
          </section>
        )}
        <EmergencyQuickCard countryCode={trip?.destination.country_code ?? null} />
        <section className="sta-card p-4" aria-labelledby="assistant-teaser">
          <div className="flex items-center gap-2">
            <Bot className="text-primary-deep" aria-hidden />
            <h2 id="assistant-teaser" className="text-lg font-extrabold text-navy">Travel Assistant</h2>
            <span className="ml-auto text-xs text-primary-deep">● Online</span>
          </div>
          <div className="mt-2 flex items-end gap-2">
            <div className="flex-1 flex flex-col gap-1">
              <p className="rounded-2xl rounded-bl-sm bg-surface-secondary px-3 py-2 text-sm text-navy w-fit">
                {rec.data ? rec.data.short_summary : "Ask me about your trip, safety or weather."}
              </p>
              {rec.data?.alternatives?.length ? <p className="rounded-2xl rounded-bl-sm bg-mint px-3 py-2 text-sm text-navy w-fit">Want me to update your plan?</p> : null}
            </div>
            <Image src="/assets/mascot/mascot-welcome.png" alt="" width={96} height={96} className="drop-shadow" />
          </div>
          <form
            className="mt-3 flex items-center gap-2 rounded-2xl border border-line bg-white px-3 py-1.5"
            onSubmit={(e) => {
              e.preventDefault();
              askAssistant();
            }}
          >
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Ask me about your trip, safety, weather…"
              aria-label="Ask the assistant"
              className="flex-1 bg-transparent text-sm outline-none"
              maxLength={2000}
            />
            <button type="submit" aria-label="Send" disabled={createConversation.isPending} className="rounded-full bg-primary p-2 text-white disabled:opacity-50">
              <Send size={16} aria-hidden />
            </button>
          </form>
        </section>
      </div>

      <DetailSheet open={sheet !== null} title={sheet === "weather" ? "Weather along your route" : sheet === "transport" ? "Transport status" : "Risk assessment"} onClose={() => setSheet(null)}>
        {!rec.data ? (
          <p className="text-sm text-ink-muted">No live data yet. Run an assessment from My Trip.</p>
        ) : sheet === "weather" ? (
          <>
            <p className="text-2xl font-extrabold text-weather">{weatherHeadline(rec.data).value}</p>
            <dl className="grid grid-cols-2 gap-2 text-sm">
              {Object.entries((rec.data.weather_summary ?? {}) as Record<string, unknown>).map(([k, v]) => (
                <div key={k} className="rounded-xl bg-surface-secondary p-2">
                  <dt className="text-xs text-ink-muted">{k.replace("weather_", "").replaceAll("_", " ")}</dt>
                  <dd className="font-bold text-navy">{v == null ? "—" : String(typeof v === "number" ? Math.round(v * 10) / 10 : v)}</dd>
                </div>
              ))}
            </dl>
            <DataFreshness fetchedAt={rec.data.freshness.fetched_at} />
            <SourceList sources={rec.data.sources?.filter((s) => s.provider.includes("meteo"))} />
          </>
        ) : sheet === "transport" ? (
          <>
            <p className="text-2xl font-extrabold text-primary-deep">{transportHeadline(rec.data).value}</p>
            <p className="text-sm text-ink-muted">{transportHeadline(rec.data).detail}</p>
            <DataFreshness fetchedAt={rec.data.freshness.fetched_at} />
          </>
        ) : (
          <>
            <RiskBadge level={rec.data.risk_level} size="lg" />
            <p className="text-navy">{rec.data.short_summary}</p>
            <ul className="list-disc pl-5 text-sm">{rec.data.reasons?.map((r) => <li key={r}>{r}</li>)}</ul>
            <p className="text-xs text-ink-muted">Reason codes: {rec.data.reason_codes?.join(", ") || "—"}</p>
            <SourceList sources={rec.data.sources} />
          </>
        )}
      </DetailSheet>

      <DetailSheet open={marker !== null} title={marker?.label ?? "Alert"} onClose={() => setMarker(null)}>
        {marker?.kind === "event" && marker.data ? (
          <AlertDetail data={marker.data as { severity?: string; area?: string | null; source_url?: string | null; observed_at?: string | null; fetched_at?: string | null; official?: boolean; event_type?: string }} />
        ) : (
          <p className="text-sm text-ink-muted">{marker?.label}</p>
        )}
      </DetailSheet>
    </div>
  );
}

function AlertDetail({ data }: { data: { severity?: string; area?: string | null; source_url?: string | null; observed_at?: string | null; fetched_at?: string | null; official?: boolean; event_type?: string } }) {
  return (
    <>
      <p className="text-sm">Severity: <strong>{data.severity ?? "UNKNOWN"}</strong> {data.official ? "· official source" : ""}</p>
      {data.area && <p className="text-sm">Area: {data.area}</p>}
      {data.event_type && <p className="text-sm">Type: {data.event_type}</p>}
      <DataFreshness fetchedAt={data.fetched_at ?? data.observed_at} />
      {data.source_url && (
        <a href={data.source_url} target="_blank" rel="noopener noreferrer" className="text-sm text-primary-deep underline">Open source</a>
      )}
    </>
  );
}
