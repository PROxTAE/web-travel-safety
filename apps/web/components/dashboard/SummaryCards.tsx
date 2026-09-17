"use client";

import { AlertTriangle, ChevronRight, CloudRain, TrainFront } from "lucide-react";
import type { ReactNode } from "react";

import { RiskBadge } from "@/components/ui/primitives";
import type { RecommendationResponse } from "@/lib/api/generated/contracts";
import { RISK_LABELS, cn } from "@/lib/utils";

type Props = { rec: RecommendationResponse | null | undefined; loading: boolean; onOpen: (sheet: "weather" | "transport" | "risk") => void };

export function weatherHeadline(rec: RecommendationResponse | null | undefined): { value: string; detail: string } {
  const w = rec?.weather_summary as Record<string, number | null | undefined> | null | undefined;
  if (!w) return { value: "No data", detail: "Run an assessment to load live weather" };
  const t = w.weather_max_temperature_c;
  const p = w.weather_max_precip_mm;
  const wind = w.weather_max_wind_gust_kmh;
  const cond = p != null && p >= 10 ? "Heavy rain" : p != null && p > 0.5 ? "Rain" : wind != null && wind >= 60 ? "Windy" : "Clear";
  const value = t != null ? `${Math.round(t)}°C · ${cond}` : cond;
  const detail =
    p != null && p > 0 ? `Up to ${p.toFixed(1)} mm along the route` : wind != null ? `Gusts up to ${Math.round(wind)} km/h` : "Along your route";
  return { value, detail };
}

export function transportHeadline(rec: RecommendationResponse | null | undefined): { value: string; detail: string } {
  const t = rec?.transport_summary as { records?: number; statuses?: string[] } | null | undefined;
  if (!t || !t.records) return { value: "No live status", detail: "Transit status unavailable for this corridor" };
  const s = t.statuses ?? [];
  const value = s.includes("CANCELLED") ? "Cancellations" : s.includes("DISRUPTED") ? "Disrupted" : s.includes("DELAYED") ? "Delays" : "On time";
  return { value, detail: `${t.records} services checked` };
}

export function SummaryCards({ rec, loading, onOpen }: Props) {
  const weather = weatherHeadline(rec);
  const transport = transportHeadline(rec);
  const risk = rec ? RISK_LABELS[rec.risk_level] : null;
  return (
    <div className="grid gap-4 sm:grid-cols-3">
      <SummaryCard
        icon={<CloudRain aria-hidden />}
        tone="bg-weather/10 text-weather"
        label="Weather"
        value={weather.value}
        valueClass="text-weather"
        detail={weather.detail}
        loading={loading}
        onPress={() => onOpen("weather")}
      />
      <SummaryCard
        icon={<TrainFront aria-hidden />}
        tone="bg-mint text-primary-deep"
        label="Transport"
        value={transport.value}
        valueClass="text-primary-deep"
        detail={transport.detail}
        loading={loading}
        onPress={() => onOpen("transport")}
      />
      <SummaryCard
        icon={<AlertTriangle aria-hidden />}
        tone={risk?.tone === "danger" ? "bg-coral/10 text-coral" : risk?.tone === "warning" ? "bg-amber/15 text-amber" : "bg-mint text-primary-deep"}
        label="Risk Level"
        value={rec ? <RiskBadge level={rec.risk_level} size="lg" /> : "No assessment"}
        detail={rec ? rec.short_summary : "Plan a trip to get a risk assessment"}
        loading={loading}
        onPress={() => onOpen("risk")}
      />
    </div>
  );
}

function SummaryCard({
  icon,
  tone,
  label,
  value,
  valueClass,
  detail,
  loading,
  onPress,
}: {
  icon: ReactNode;
  tone: string;
  label: string;
  value: ReactNode;
  valueClass?: string;
  detail: string;
  loading: boolean;
  onPress: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onPress}
      className="sta-card flex items-center gap-4 p-4 text-left hover:border-primary/40 transition-colors"
      aria-busy={loading}
    >
      <span className={cn("sta-icon-tile", tone)}>{icon}</span>
      <span className="flex-1 min-w-0">
        <span className="block text-sm text-ink-muted">{label}</span>
        <span className={cn("block text-xl font-extrabold truncate", valueClass)}>{loading ? "…" : value}</span>
        <span className="block text-xs text-ink-muted truncate">{detail}</span>
      </span>
      <ChevronRight className="text-ink-muted" aria-hidden />
    </button>
  );
}
