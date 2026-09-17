import type { ActionCode, RiskLevel, RunStage, Severity, TravelMode } from "@/lib/api/generated/contracts";

export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return h > 0 ? `${h} hr ${m} min` : `${m} min`;
}

export function formatDistance(meters: number): string {
  return meters >= 1000 ? `${Math.round(meters / 1000)} km` : `${Math.round(meters)} m`;
}

export function formatTime(iso: string | null | undefined, timeZone?: string, locale = "en-GB"): string {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", timeZone }).format(new Date(iso));
  } catch {
    return "—";
  }
}

export function formatDate(iso: string | null | undefined, timeZone?: string, locale = "en-GB"): string {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeZone }).format(new Date(iso));
  } catch {
    return "—";
  }
}

/** "Updated 8 min ago" — relative freshness label; never hides age. */
export function relativeAge(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "unknown";
  const diff = Math.max(0, now - new Date(iso).getTime());
  const min = Math.round(diff / 60_000);
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  const h = Math.round(min / 60);
  if (h < 24) return `${h} hr ago`;
  return `${Math.round(h / 24)} d ago`;
}

export function isStale(iso: string | null | undefined, maxAgeMinutes: number, now = Date.now()): boolean {
  if (!iso) return true;
  return now - new Date(iso).getTime() > maxAgeMinutes * 60_000;
}

/** Display mapping only — the action itself always comes from the server. */
export const ACTION_LABELS: Record<ActionCode, { title: string; tone: "success" | "warning" | "danger" | "accent" }> = {
  NORMAL: { title: "Proceed as planned", tone: "success" },
  CHANGE_ROUTE: { title: "Change route", tone: "warning" },
  DELAY: { title: "Delay departure", tone: "warning" },
  AVOID: { title: "Avoid travel", tone: "danger" },
};

export const RISK_LABELS: Record<RiskLevel, { title: string; tone: "success" | "warning" | "danger" | "default"; icon: string }> = {
  LOW: { title: "Low", tone: "success", icon: "🛡️" },
  MEDIUM: { title: "Medium", tone: "warning", icon: "⚠️" },
  HIGH: { title: "High", tone: "danger", icon: "⛔" },
  UNKNOWN: { title: "Unknown", tone: "default", icon: "❔" },
};

export const SEVERITY_TONE: Record<Severity, "success" | "warning" | "danger" | "default"> = {
  INFO: "default",
  MINOR: "success",
  MODERATE: "warning",
  SEVERE: "danger",
  EXTREME: "danger",
  UNKNOWN: "default",
};

export const STAGE_LABELS: Record<RunStage, string> = {
  VALIDATING: "Checking your trip details",
  FETCHING_EXTERNAL_DATA: "Checking weather, transport and hazards",
  INTEGRATING_DATA: "Combining live data",
  ASSESSING_RISK: "Assessing route risk",
  RETRIEVING_GUIDANCE: "Looking up official guidance",
  EVALUATING_ROUTES: "Comparing route options",
  MAKING_DECISION: "Deciding the safest option",
  EXPLAINING: "Writing the explanation",
  FORMATTING_RESPONSE: "Preparing your recommendation",
};

export const STAGE_ORDER: RunStage[] = [
  "VALIDATING",
  "FETCHING_EXTERNAL_DATA",
  "INTEGRATING_DATA",
  "ASSESSING_RISK",
  "RETRIEVING_GUIDANCE",
  "EVALUATING_ROUTES",
  "MAKING_DECISION",
  "EXPLAINING",
  "FORMATTING_RESPONSE",
];

export function stagePercent(stage: RunStage | null | undefined): number {
  if (!stage) return 0;
  const i = STAGE_ORDER.indexOf(stage);
  return i < 0 ? 0 : Math.round(((i + 1) / STAGE_ORDER.length) * 100);
}

export const MODE_LABELS: Record<TravelMode, string> = {
  FLIGHT: "Flight",
  TRAIN: "Train",
  BUS: "Bus",
  CAR: "Car",
  WALK: "Walk",
  BICYCLE: "Bicycle",
  MULTIMODAL: "Multimodal",
};

export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}
