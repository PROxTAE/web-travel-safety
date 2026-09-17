"use client";

import { Button } from "@heroui/react";
import { Bell, Car, CheckCircle2, ChevronRight, Clock, CloudLightning, Route, Star } from "lucide-react";
import Image from "next/image";
import { useRouter, useSearchParams } from "next/navigation";
import { use, useMemo, useState } from "react";

import { DynamicMap } from "@/components/map/DynamicMap";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataFreshness, DegradedBanner, ErrorState, RiskBadge, SourceList } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";
import { useApplyRoute } from "@/features/assessment/hooks";
import { useConsents, useGrantConsent } from "@/features/emergency/hooks";
import { useSubscribe, useUnsubscribe } from "@/features/feedback/hooks";
import { useRecommendation, useTrip } from "@/features/trips/hooks";
import { ApiError } from "@/lib/api/client";
import type { RouteCandidate } from "@/lib/api/generated/contracts";
import { boundsOf, routeToMapRoute, tripMarkers } from "@/lib/map";
import { formatDistance, formatDuration } from "@/lib/utils";

/** Screen 06. Both routes and all metrics come from the recommendation; the client only lays them side by side. */
export default function ComparePage({ params }: { params: Promise<{ tripId: string }> }) {
  const { tripId } = use(params);
  const search = useSearchParams();
  const router = useRouter();
  const toast = useToast();
  const trip = useTrip(tripId);
  const recId = search.get("recommendation") ?? trip.data?.latest_recommendation_id ?? null;
  const rec = useRecommendation(recId);
  const apply = useApplyRoute(tripId);
  const [ack, setAck] = useState(false);
  const [applied, setApplied] = useState<string | null>(null);

  const { original, safer } = useMemo(() => {
    const r = rec.data;
    if (!r) return { original: null, safer: null };
    const all = [r.primary_route, ...(r.alternatives ?? [])].filter((x): x is RouteCandidate => Boolean(x));
    const candidateId = search.get("candidate");
    const orig = all.find((x) => x.label === "ORIGINAL" || (x.labels ?? []).includes("ORIGINAL")) ?? r.primary_route ?? all[0] ?? null;
    const cand = (candidateId ? all.find((x) => x.route_id === candidateId) : null) ?? all.find((x) => x !== orig && x.usable !== false) ?? null;
    return { original: orig, safer: cand };
  }, [rec.data, search]);

  const routes = useMemo(() => {
    const out = [];
    if (original) out.push(routeToMapRoute(original, "original"));
    if (safer) out.push(routeToMapRoute(safer, "safer"));
    return out;
  }, [original, safer]);
  const markers = useMemo(() => (trip.data ? tripMarkers(trip.data) : []), [trip.data]);
  const fit = useMemo(() => boundsOf(routes.flatMap((r) => r.coordinates)), [routes]);

  const applyRoute = (route: RouteCandidate, acknowledge: boolean) => {
    if (!trip.data || !rec.data) return;
    apply.mutate(
      { revision: trip.data.revision, route_id: route.route_id, recommendation_id: rec.data.recommendation_id, acknowledge_risk: acknowledge },
      {
        onSuccess: (res) => {
          setApplied(route.route_id);
          toast.push({ tone: "success", title: "Trip updated", description: "Your route has been changed and a new assessment started." });
          router.push(`/trips/${tripId}?run=${res.run.request_id}`);
        },
        onError: (e) => {
          const err = e instanceof ApiError ? e : null;
          toast.push({
            tone: err?.code === "PRECONDITION_FAILED" ? "warning" : "danger",
            title: err?.code === "PRECONDITION_FAILED" ? "Trip changed elsewhere — reload" : "Could not apply the route",
            description: err?.message,
          });
          if (err?.code === "PRECONDITION_FAILED") void trip.refetch();
        },
      },
    );
  };

  if (trip.isError) return <ErrorState error={trip.error} onRetry={() => trip.refetch()} />;
  if (rec.isError) return <ErrorState error={rec.error} onRetry={() => rec.refetch()} />;
  if (!trip.data || !rec.data) return <div className="h-96 animate-pulse rounded-2xl bg-white/60" aria-busy="true" />;

  const deltaMin = original && safer ? Math.round((safer.duration_seconds - original.duration_seconds) / 60) : null;
  const originalRisky = original?.risk_level === "MEDIUM" || original?.risk_level === "HIGH" || rec.data.action_code === "AVOID";
  const originalIsSelected = applied === original?.route_id || (applied === null && trip.data.selected_route_id === original?.route_id);

  return (
    <>
      <PageHeader
        title="Compare Routes"
        subtitle="Choose the option that fits your trip"
        icon={<Route aria-hidden />}
        aside={
          deltaMin !== null &&
          safer && (
            <div className="sta-card flex items-center gap-3 px-4 py-3">
              <span className="sta-icon-tile !w-10 !h-10 bg-mint text-primary-deep"><Clock size={18} aria-hidden /></span>
              <div>
                <p className="text-xl font-extrabold text-navy">
                  <span className="text-primary">{deltaMin >= 0 ? "+" : ""}{deltaMin} min</span> · <RiskBadge level={safer.risk_level} size="sm" />
                </p>
                <p className="text-xs text-ink-muted">{deltaMin >= 0 ? "A safer journey for greater peace of mind" : "Faster and safer"}</p>
              </div>
            </div>
          )
        }
      />
      <div className="grid gap-4 xl:grid-cols-[1fr_1.4fr_1fr]">
        <RouteColumn
          title="Original route"
          dot="bg-[#8b93a7]"
          route={original}
          exposureTitle={(rec.data.reason_codes ?? []).includes("SEVERE_WEATHER_CORRIDOR") ? "Storm exposure" : "Hazard exposure"}
          exposureText={exposureText(original)}
          exposureIcon={<CloudLightning aria-hidden />}
          footer={
            original && (
              <>
                <Button variant="outline" fullWidth isDisabled={apply.isPending || originalIsSelected || (originalRisky && !ack)} onPress={() => applyRoute(original, originalRisky)}>
                  {originalIsSelected ? "Original route selected" : "Keep original"}
                </Button>
                {originalRisky && (
                  <label className="mt-3 flex gap-2 rounded-2xl border border-amber/60 bg-amber/10 p-3 text-sm text-navy">
                    <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} className="mt-1 h-4 w-4 accent-primary" />
                    <span>
                      <strong>This route passes through severe conditions. Continue only if necessary.</strong>
                      <br />I understand the risk and want to keep the original route.
                    </span>
                  </label>
                )}
              </>
            )
          }
        />

        <section className="sta-card p-3 min-h-[28rem] relative" aria-label="Route comparison map">
          <DynamicMap ariaLabel="Original and safer routes" routes={routes} markers={markers} fitTo={fit} />
          <div className="absolute bottom-5 left-5 rounded-xl bg-white/95 px-3 py-2 text-[11px] text-ink-muted shadow-card">
            <p><span className="inline-block w-6 border-t-4 border-primary align-middle mr-1" />Safer route {safer && `(${safer.risk_level ?? "?"} risk)`}</p>
            <p><span className="inline-block w-6 border-t-4 border-dotted border-[#8b93a7] align-middle mr-1" />Original route {original && `(${original.risk_level ?? "?"} risk)`}</p>
          </div>
        </section>

        <RouteColumn
          title="Safer route"
          dot="bg-primary"
          badge={safer ? <span className="inline-flex items-center gap-1 rounded-pill bg-primary px-3 py-1 text-xs font-bold text-white"><Star size={12} aria-hidden />Recommended</span> : null}
          route={safer}
          exposureTitle={safer?.exposure && (safer.exposure.severe_weather_minutes ?? 0) === 0 ? "Avoids severe weather" : "Lower exposure"}
          exposureText={safer ? exposureText(safer) : "No alternative returned by the server"}
          exposureIcon={<CheckCircle2 aria-hidden />}
          footer={
            safer ? (
              <>
                <Button fullWidth size="lg" isDisabled={apply.isPending || applied === safer.route_id} onPress={() => applyRoute(safer, false)}>
                  <Route size={18} aria-hidden /> {apply.isPending ? "Applying…" : applied === safer.route_id ? "Applied" : "Apply safer route"} <ChevronRight size={16} aria-hidden />
                </Button>
                <div className="mt-3 flex items-end gap-2">
                  <Image src="/assets/mascot/mascot-welcome.png" alt="" width={110} height={110} />
                  <p className="rounded-2xl rounded-bl-sm bg-white px-3 py-2 text-sm text-navy shadow-card">A little longer, much safer.</p>
                </div>
              </>
            ) : (
              <p className="text-sm text-ink-muted">The server did not return a usable alternative for this trip.</p>
            )
          }
        />

        <div className="xl:col-span-3 sta-card flex flex-wrap items-center gap-4 p-4">
          <span className="sta-icon-tile bg-mint text-primary-deep"><CheckCircle2 aria-hidden /></span>
          <div className="flex-1 min-w-60">
            <p className="font-bold text-navy">Your itinerary, alerts, and transport details will update automatically.</p>
            <p className="text-sm text-ink-muted">We&apos;ll adjust your schedule and keep you informed about any changes.</p>
          </div>
          <NotifyToggle tripId={tripId} />
        </div>
        <div className="xl:col-span-3 flex flex-col gap-2">
          <DegradedBanner services={rec.data.degraded_services} limitations={rec.data.limitations} />
          <details className="text-sm">
            <summary className="cursor-pointer font-semibold text-primary-deep">Route data sources</summary>
            <div className="mt-2"><SourceList sources={[...(original?.sources ?? []), ...(safer?.sources ?? [])]} /></div>
          </details>
        </div>
      </div>
    </>
  );
}

function exposureText(route: RouteCandidate | null): string {
  const x = route?.exposure;
  if (!x) return route?.trade_offs?.join(" · ") ?? "No exposure data";
  const parts = [
    `${x.severe_weather_minutes ?? 0} min in severe weather`,
    `${x.hazard_event_ids?.length ?? 0} hazard event${(x.hazard_event_ids?.length ?? 0) === 1 ? "" : "s"}`,
  ];
  if (x.closed) parts.push("official closure on route");
  if (x.min_hazard_distance_km != null) parts.push(`nearest hazard ${x.min_hazard_distance_km.toFixed(0)} km`);
  return parts.join(" · ");
}

function RouteColumn({
  title,
  dot,
  badge,
  route,
  exposureTitle,
  exposureText,
  exposureIcon,
  footer,
}: {
  title: string;
  dot: string;
  badge?: React.ReactNode;
  route: RouteCandidate | null;
  exposureTitle: string;
  exposureText: string;
  exposureIcon: React.ReactNode;
  footer: React.ReactNode;
}) {
  return (
    <section className="sta-card p-4 flex flex-col" aria-label={title}>
      <div className="flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-lg font-extrabold text-navy">
          <span className={`inline-block h-4 w-4 rounded-full ${dot}`} aria-hidden /> {title}
        </h2>
        {badge}
      </div>
      {route ? (
        <>
          <p className="mt-3 text-3xl font-extrabold text-navy">{formatDuration(route.duration_seconds)}</p>
          <p className="flex items-center gap-2 text-sm text-ink-muted">
            <Car size={16} aria-hidden /> {formatDistance(route.distance_m)} · {route.transfers ?? 0} stops
          </p>
          <hr className="my-3 border-line" />
          <div className="flex items-center gap-3">
            <RiskBadge level={route.risk_level} size="lg" />
            {route.risk_score != null && <span className="text-xs text-ink-muted">score {(route.risk_score * 100).toFixed(0)}</span>}
          </div>
          <div className="mt-3 flex items-start gap-3 rounded-2xl bg-surface-secondary p-3">
            <span className="sta-icon-tile !w-10 !h-10 bg-white text-navy">{exposureIcon}</span>
            <div>
              <p className="font-bold text-navy">{exposureTitle}</p>
              <p className="text-sm text-ink-muted">{exposureText}</p>
            </div>
          </div>
          <p className="mt-2"><DataFreshness fetchedAt={route.sources?.[0]?.fetched_at} /></p>
        </>
      ) : (
        <p className="mt-3 text-sm text-ink-muted">Not available.</p>
      )}
      <div className="mt-auto pt-4">{footer}</div>
    </section>
  );
}

function NotifyToggle({ tripId }: { tripId: string }) {
  const consents = useConsents();
  const grant = useGrantConsent();
  const subscribe = useSubscribe();
  const unsubscribe = useUnsubscribe();
  const toast = useToast();
  const [subId, setSubId] = useState<string | null>(null);
  const on = subId !== null;
  const busy = grant.isPending || subscribe.isPending || unsubscribe.isPending;

  const toggle = async () => {
    try {
      if (on) {
        await unsubscribe.mutateAsync(subId);
        setSubId(null);
        return;
      }
      let consent = consents.data?.find((c) => c.type === "ALERT_NOTIFICATION" && c.granted && !c.revoked_at);
      if (!consent) consent = await grant.mutateAsync({ type: "ALERT_NOTIFICATION", granted: true });
      const sub = await subscribe.mutateAsync({ trip_id: tripId, consent_id: consent.id });
      setSubId(sub.id);
      toast.push({ tone: "success", title: "Alerts on", description: "We'll notify you in-app about new risks or better options." });
    } catch (e) {
      toast.push({ tone: "danger", title: "Could not update alerts", description: e instanceof ApiError ? e.message : undefined });
    }
  };

  return (
    <label className="flex items-center gap-3 rounded-2xl border border-line px-4 py-2">
      <Bell className="text-navy" aria-hidden />
      <span>
        <span className="block font-semibold text-navy">Notify me about changes</span>
        <span className="block text-xs text-ink-muted">Get updates if there are new risks or better options.</span>
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-label="Notify me about changes"
        disabled={busy}
        onClick={toggle}
        className={`relative h-7 w-12 rounded-full transition-colors ${on ? "bg-primary" : "bg-line"} disabled:opacity-50`}
      >
        <span className={`absolute top-1 h-5 w-5 rounded-full bg-white shadow transition-all ${on ? "left-6" : "left-1"}`} />
      </button>
    </label>
  );
}
