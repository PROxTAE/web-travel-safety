"use client";

import { ChevronRight, Lightbulb, Route, ThumbsDown, ThumbsUp } from "lucide-react";
import { useState } from "react";

import { ActionBadge, ButtonLink, DataFreshness, DegradedBanner, RiskBadge, SourceList } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";
import { useSendFeedback } from "@/features/feedback/hooks";
import type { RecommendationResponse } from "@/lib/api/generated/contracts";
import { ACTION_LABELS } from "@/lib/utils";

/** Shows the server's locked decision. The client never derives or softens the action. */
export function RecommendationCard({ rec, tripId, compact }: { rec: RecommendationResponse; tripId: string; compact?: boolean }) {
  const meta = ACTION_LABELS[rec.action_code];
  const saferRoute = rec.alternatives?.find((a) => a.usable !== false && a.route_id !== rec.primary_route?.route_id);
  const compareHref = `/trips/${tripId}/compare?recommendation=${rec.recommendation_id}${saferRoute ? `&candidate=${saferRoute.route_id}` : ""}`;
  return (
    <section className="sta-card p-4 lg:p-5" aria-labelledby={`rec-${rec.recommendation_id}`}>
      <div className="flex items-center justify-between gap-2">
        <h2 id={`rec-${rec.recommendation_id}`} className="flex items-center gap-2 text-lg font-extrabold text-navy">
          <Lightbulb className="text-amber" aria-hidden /> AI Recommendation
        </h2>
        <DataFreshness fetchedAt={rec.freshness.fetched_at} expiresAt={rec.expires_at} maxAgeMinutes={90} />
      </div>
      <div
        className={`mt-3 rounded-2xl border p-4 ${
          meta.tone === "danger" ? "border-coral/50 bg-coral/5" : meta.tone === "warning" ? "border-amber/60 bg-amber/10" : "border-primary/40 bg-mint/60"
        }`}
      >
        <div className="flex flex-wrap items-center gap-2">
          <ActionBadge action={rec.action_code} />
          <RiskBadge level={rec.risk_level} size="sm" />
          <span className="text-xs text-ink-muted">confidence {(rec.confidence * 100).toFixed(0)}%</span>
        </div>
        <p className="mt-2 text-navy font-semibold">{rec.short_summary}</p>
        {!compact && rec.reasons && rec.reasons.length > 0 && (
          <ul className="mt-2 list-disc pl-5 text-sm text-ink">
            {rec.reasons.slice(0, 4).map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        )}
        {rec.immediate_actions && rec.immediate_actions.length > 0 && (
          <ol className="mt-2 flex flex-col gap-1 text-sm">
            {rec.immediate_actions.slice(0, 3).map((a, i) => (
              <li key={a} className="flex gap-2">
                <span className="flex h-5 w-5 flex-none items-center justify-center rounded-full bg-primary text-[11px] font-bold text-white">{i + 1}</span>
                {a}
              </li>
            ))}
          </ol>
        )}
        {(rec.action_code === "CHANGE_ROUTE" || saferRoute) && (
          <ButtonLink href={compareHref} fullWidth className="mt-3">
            <Route size={18} aria-hidden /> Use safer route <ChevronRight size={16} aria-hidden />
          </ButtonLink>
        )}
      </div>
      <div className="mt-3">
        <DegradedBanner services={rec.degraded_services} limitations={rec.limitations} />
      </div>
      {!compact && (
        <details className="mt-3 text-sm">
          <summary className="cursor-pointer font-semibold text-primary-deep">Sources &amp; versions</summary>
          <div className="mt-2 flex flex-col gap-2">
            <SourceList sources={rec.sources} />
            <p className="text-xs text-ink-muted">
              policy {rec.versions.policy ?? "—"} · model {rec.versions.model ?? "—"} · knowledge {rec.versions.knowledge_collection ?? "—"}
            </p>
          </div>
        </details>
      )}
      <FeedbackRow recommendationId={rec.recommendation_id} />
    </section>
  );
}

function FeedbackRow({ recommendationId }: { recommendationId: string }) {
  const fb = useSendFeedback();
  const toast = useToast();
  const [sent, setSent] = useState<"HELPFUL" | "INCORRECT" | null>(null);
  const send = (category: "HELPFUL" | "INCORRECT") =>
    fb.mutate(
      { recommendation_id: recommendationId, category },
      {
        onSuccess: () => {
          setSent(category);
          toast.push({ tone: "success", title: "Thanks for the feedback" });
        },
        onError: () => toast.push({ tone: "warning", title: "Feedback not sent", description: "Please try again later." }),
      },
    );
  return (
    <div className="mt-3 flex items-center gap-2 text-xs text-ink-muted">
      Was this helpful?
      <button type="button" aria-pressed={sent === "HELPFUL"} disabled={fb.isPending} onClick={() => send("HELPFUL")} className="rounded-full p-1.5 hover:bg-mint disabled:opacity-50">
        <ThumbsUp size={14} aria-hidden />
        <span className="sr-only">Helpful</span>
      </button>
      <button type="button" aria-pressed={sent === "INCORRECT"} disabled={fb.isPending} onClick={() => send("INCORRECT")} className="rounded-full p-1.5 hover:bg-coral/10 disabled:opacity-50">
        <ThumbsDown size={14} aria-hidden />
        <span className="sr-only">Not accurate</span>
      </button>
    </div>
  );
}
