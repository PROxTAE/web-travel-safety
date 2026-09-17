"use client";

import { Button } from "@heroui/react";
import { buttonVariants, type ButtonVariants } from "@heroui/styles";
import Link from "next/link";
import { AlertTriangle, Clock, ExternalLink, Inbox, RefreshCw, ShieldCheck, ShieldQuestion, ShieldX } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { ApiError } from "@/lib/api/client";
import type { ActionCode, RiskLevel, SourceProvenance } from "@/lib/api/generated/contracts";
import { ACTION_LABELS, RISK_LABELS, cn, isStale, relativeAge } from "@/lib/utils";

/** Clock that ticks once a minute so relative ages stay honest without impure reads during render. */
export function useNow(intervalMs = 60_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}

/** Next.js Link styled as a HeroUI button (React Aria buttons cannot render as anchors). */
export function ButtonLink({
  href,
  children,
  variant = "primary",
  size = "md",
  fullWidth,
  className,
  ...rest
}: { href: string; children: ReactNode; className?: string } & ButtonVariants & Omit<React.ComponentProps<typeof Link>, "href" | "className">) {
  return (
    <Link href={href} className={cn(buttonVariants({ variant, size, fullWidth }), className)} {...rest}>
      {children}
    </Link>
  );
}

const TONE_CLASS = {
  success: "bg-mint text-primary-deep",
  warning: "bg-amber/15 text-amber-ink",
  danger: "bg-coral/15 text-[#b3232a]",
  default: "bg-line text-ink-muted",
  accent: "bg-weather/15 text-weather-ink",
};

/** Risk level as icon + shape + text (never colour alone). The level comes from the server. */
export function RiskBadge({ level, size = "md" }: { level: RiskLevel | null | undefined; size?: "sm" | "md" | "lg" }) {
  const lv = level ?? "UNKNOWN";
  const meta = RISK_LABELS[lv];
  const Icon = lv === "LOW" ? ShieldCheck : lv === "UNKNOWN" ? ShieldQuestion : lv === "HIGH" ? ShieldX : AlertTriangle;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-pill font-bold uppercase tracking-wide",
        TONE_CLASS[meta.tone],
        size === "sm" ? "px-2 py-0.5 text-xs" : size === "lg" ? "px-3 py-1.5 text-base" : "px-2.5 py-1 text-sm",
      )}
      data-risk={lv}
    >
      <Icon size={size === "sm" ? 12 : 16} aria-hidden />
      {meta.title} risk
    </span>
  );
}

/** Action code rendered as UI token; the action is never derived on the client. */
export function ActionBadge({ action }: { action: ActionCode }) {
  const meta = ACTION_LABELS[action];
  return (
    <span className={cn("inline-flex items-center rounded-pill px-3 py-1 text-sm font-bold", TONE_CLASS[meta.tone])} data-action={action}>
      {meta.title}
    </span>
  );
}

export function DataFreshness({
  fetchedAt,
  expiresAt,
  maxAgeMinutes = 60,
  label = "Updated",
}: {
  fetchedAt: string | null | undefined;
  expiresAt?: string | null;
  maxAgeMinutes?: number;
  label?: string;
}) {
  const now = useNow();
  const stale = isStale(fetchedAt, maxAgeMinutes, now) || (expiresAt ? new Date(expiresAt).getTime() < now : false);
  return (
    <span className={cn("inline-flex items-center gap-1 text-xs", stale ? "text-amber-ink" : "text-ink-muted")}>
      <Clock size={12} aria-hidden />
      {label} {relativeAge(fetchedAt, now)}
      {stale && <span className="font-semibold">· may be stale</span>}
    </span>
  );
}

export function SourceList({ sources, limit = 5 }: { sources: SourceProvenance[] | undefined; limit?: number }) {
  if (!sources?.length) return <p className="text-xs text-ink-muted">No source metadata available.</p>;
  return (
    <ul className="flex flex-col gap-1 text-xs">
      {sources.slice(0, limit).map((s) => (
        <li key={s.source_id} className="flex items-center gap-2 text-ink-muted">
          <span className="rounded bg-line px-1.5 py-0.5 text-[10px] font-semibold uppercase">{(s.authority ?? "UNKNOWN").replaceAll("_", " ")}</span>
          <a href={s.source_url ?? undefined} target="_blank" rel="noopener noreferrer" className="underline decoration-dotted hover:text-navy inline-flex items-center gap-1">
            {s.provider}
            <ExternalLink size={10} aria-hidden />
          </a>
          <span>· observed {relativeAge(s.observed_at)}</span>
        </li>
      ))}
    </ul>
  );
}

export function DegradedBanner({ services, limitations }: { services?: string[] | null; limitations?: string[] | null }) {
  const items = [...(services ?? []), ...(limitations ?? [])];
  if (!items.length) return null;
  return (
    <div role="status" className="rounded-2xl border border-amber/50 bg-amber/10 px-4 py-3 text-sm text-navy">
      <p className="font-semibold flex items-center gap-2">
        <AlertTriangle size={16} aria-hidden /> Some data is limited
      </p>
      <ul className="mt-1 list-disc pl-5 text-ink-muted">
        {items.map((i) => (
          <li key={i}>{humanizeLimitation(i)}</li>
        ))}
      </ul>
    </div>
  );
}

export function humanizeLimitation(raw: string): string {
  return raw.replace(/^UNAVAILABLE_CAPABILITY:/, "Unavailable: ").replaceAll("_", " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
}

export function EmptyState({ title, description, action }: { title: string; description?: string; action?: ReactNode }) {
  return (
    <div className="sta-card p-8 text-center">
      <span className="sta-icon-tile mx-auto bg-mint text-primary-deep">
        <Inbox aria-hidden />
      </span>
      <h3 className="mt-3 font-bold text-navy">{title}</h3>
      {description && <p className="mt-1 text-sm text-ink-muted">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry, compact }: { error: unknown; onRetry?: () => void; compact?: boolean }) {
  const e = error instanceof ApiError ? error : null;
  const title =
    e?.code === "RATE_LIMITED"
      ? "Too many requests"
      : e?.code === "DEPENDENCY_TIMEOUT"
        ? "The service is taking too long"
        : e?.code === "NETWORK_ERROR"
          ? "You appear to be offline"
          : e?.status === 404
            ? "Not found"
            : "Something went wrong";
  return (
    <div role="alert" className={cn("rounded-2xl border border-coral/40 bg-coral/5", compact ? "p-3" : "p-6 text-center")}>
      <p className="font-bold text-navy">{title}</p>
      {e && (
        <p className="text-sm text-ink-muted mt-1">
          {e.message}
          {e.retryAfterSeconds ? ` · retry in ${e.retryAfterSeconds}s` : ""}
          {e.requestId ? ` · ref ${e.requestId.slice(0, 8)}` : ""}
        </p>
      )}
      {onRetry && (e?.retryable ?? true) && (
        <Button size="sm" variant="secondary" className="mt-3" onPress={onRetry}>
          <RefreshCw size={14} aria-hidden /> Retry
        </Button>
      )}
    </div>
  );
}

export function SectionTitle({ icon, children, aside }: { icon?: ReactNode; children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-2 mb-3">
      <h2 className="flex items-center gap-2 text-lg font-extrabold text-navy">
        {icon && <span className="text-primary-deep">{icon}</span>}
        {children}
      </h2>
      {aside}
    </div>
  );
}
