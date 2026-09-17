"use client";

import { ChevronRight, Gauge, Plane, Route, ShieldCheck, Shuffle, Timer, UserRound } from "lucide-react";
import Link from "next/link";

import { DataFreshness, RiskBadge } from "@/components/ui/primitives";
import type { RecommendationResponse, RouteCandidate, RouteLabel } from "@/lib/api/generated/contracts";
import { cn, formatDistance, formatDuration } from "@/lib/utils";

export type RouteOption = { label: RouteLabel; route: RouteCandidate | null; badge?: string };

/** Picks Recommended / Fastest / Lowest risk from server labels. Missing => shown as unavailable, never fabricated. */
export function pickRouteOptions(rec: RecommendationResponse): RouteOption[] {
  const all = [rec.primary_route, ...(rec.alternatives ?? [])].filter((r): r is RouteCandidate => Boolean(r));
  const has = (r: RouteCandidate, l: RouteLabel) => r.label === l || (r.labels ?? []).includes(l);
  const find = (l: RouteLabel) => all.find((r) => has(r, l)) ?? null;
  const recommended = find("RECOMMENDED") ?? rec.primary_route ?? null;
  return [
    { label: "RECOMMENDED", route: recommended, badge: "Best balance" },
    { label: "FASTEST", route: find("FASTEST") },
    { label: "LOWEST_RISK", route: find("LOWEST_RISK") },
  ];
}

const META: Record<RouteLabel, { title: string; icon: typeof Route; tile: string }> = {
  RECOMMENDED: { title: "Recommended", icon: UserRound, tile: "bg-mint text-primary-deep" },
  FASTEST: { title: "Fastest", icon: Plane, tile: "bg-weather/10 text-weather" },
  LOWEST_RISK: { title: "Lowest risk", icon: ShieldCheck, tile: "bg-mint text-primary-deep" },
  ORIGINAL: { title: "Original", icon: Route, tile: "bg-line text-ink-muted" },
  ALTERNATIVE: { title: "Alternative", icon: Shuffle, tile: "bg-line text-ink-muted" },
};

export function RouteOptions({ rec, tripId, unavailable }: { rec: RecommendationResponse; tripId: string; unavailable: string[] }) {
  const options = pickRouteOptions(rec);
  const routeBlocked = unavailable.some((u) => /openrouteservice|route/i.test(u));
  return (
    <section className="sta-card p-4" aria-labelledby="route-options">
      <h2 id="route-options" className="flex items-center gap-2 text-lg font-extrabold text-navy">
        <Route className="text-primary-deep" aria-hidden /> Route Options
      </h2>
      <p className="mt-1 text-xs text-ink-muted">
        <DataFreshness fetchedAt={rec.freshness.fetched_at} />
      </p>
      <div className="mt-3 flex flex-col gap-3">
        {options.map((o) => {
          const m = META[o.label];
          const Icon = m.icon;
          if (!o.route) {
            return (
              <div key={o.label} className="rounded-2xl border border-dashed border-line p-4 text-sm text-ink-muted">
                <p className="font-bold text-navy">{m.title}</p>
                <p>{routeBlocked ? "Road routing provider unavailable — no option generated." : "No route with this label was returned for this trip."}</p>
              </div>
            );
          }
          const r = o.route;
          return (
            <Link
              key={o.label}
              href={`/trips/${tripId}/compare?recommendation=${rec.recommendation_id}&candidate=${r.route_id}`}
              className={cn(
                "flex items-center gap-3 rounded-2xl border p-4 hover:border-primary transition-colors",
                o.label === "RECOMMENDED" ? "border-primary bg-white" : "border-line bg-white",
              )}
            >
              <span className={cn("sta-icon-tile", m.tile)}>
                <Icon aria-hidden />
              </span>
              <span className="flex-1 min-w-0">
                <span className="flex items-center gap-2">
                  <span className="text-lg font-extrabold text-navy">{m.title}</span>
                  {o.badge && <span className="rounded-pill bg-mint px-2 py-0.5 text-xs font-semibold text-primary-deep">{o.badge}</span>}
                  {r.usable === false && <span className="rounded-pill bg-coral/10 px-2 py-0.5 text-xs font-semibold text-coral">Not usable</span>}
                </span>
                <span className="mt-1 grid grid-cols-3 gap-2 text-xs text-ink-muted">
                  <span>
                    <span className="flex items-center gap-1 text-sm font-bold text-navy"><Timer size={14} aria-hidden />{formatDuration(r.duration_seconds)}</span>Travel time
                  </span>
                  <span>
                    <span className="flex items-center gap-1 text-sm font-bold text-navy"><Gauge size={14} aria-hidden />{r.transfers ?? 0}</span>{(r.transfers ?? 0) === 1 ? "Transfer" : "Transfers"} · {formatDistance(r.distance_m)}
                  </span>
                  <span>
                    <RiskBadge level={r.risk_level} size="sm" />
                  </span>
                </span>
              </span>
              <ChevronRight className="text-ink-muted" aria-hidden />
            </Link>
          );
        })}
      </div>
    </section>
  );
}
