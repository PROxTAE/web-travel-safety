"use client";

import { Button } from "@heroui/react";
import { AlertTriangle, CheckCircle2, Loader2, XCircle } from "lucide-react";

import type { RunProgress } from "@/features/assessment/useRunEvents";
import { STAGE_LABELS, STAGE_ORDER, stagePercent } from "@/lib/utils";

/** Progress rendered from contract stages (localized labels); never invents a stage. */
export function AssessmentProgress({
  progress,
  onCancel,
  onRetry,
  onResume,
  cancelling,
}: {
  progress: RunProgress;
  onCancel?: () => void;
  onRetry?: () => void;
  onResume?: () => void;
  cancelling?: boolean;
}) {
  const pct = progress.percent ?? stagePercent(progress.stage);
  const running = ["CONNECTING", "QUEUED", "RUNNING", "DISCONNECTED"].includes(progress.status);
  return (
    <section className="sta-card p-4" aria-live="polite" aria-label="Assessment progress">
      <div className="flex items-center gap-3">
        {running ? (
          <Loader2 className="animate-spin text-primary" aria-hidden />
        ) : progress.status === "FAILED" ? (
          <XCircle className="text-coral" aria-hidden />
        ) : progress.status === "NEEDS_INPUT" ? (
          <AlertTriangle className="text-amber" aria-hidden />
        ) : (
          <CheckCircle2 className="text-primary" aria-hidden />
        )}
        <div className="flex-1">
          <p className="font-bold text-navy">
            {progress.status === "CONNECTING"
              ? "Connecting…"
              : progress.status === "DISCONNECTED"
                ? "Reconnecting to live updates…"
                : progress.status === "QUEUED"
                  ? "Queued"
                  : progress.status === "FAILED"
                    ? "Assessment failed"
                    : progress.status === "NEEDS_INPUT"
                      ? "We need a little more information"
                      : progress.status === "CANCELLED"
                        ? "Cancelled"
                        : progress.stage
                          ? STAGE_LABELS[progress.stage]
                          : "Done"}
          </p>
          <p className="text-xs text-ink-muted">{running ? `${pct}%` : progress.status === "PARTIAL" ? "Completed with limited data" : ""}</p>
        </div>
        {running && onCancel && (
          <Button size="sm" variant="ghost" onPress={onCancel} isDisabled={cancelling}>
            Cancel
          </Button>
        )}
      </div>
      <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-surface-secondary" role="progressbar" aria-label="Assessment progress" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${pct}%` }} />
      </div>
      <ol className="mt-3 grid grid-cols-3 gap-1 text-[11px] text-ink-muted">
        {STAGE_ORDER.filter((_, i) => i % 3 === 1).map((s) => (
          <li key={s} className={STAGE_ORDER.indexOf(s) <= STAGE_ORDER.indexOf(progress.stage ?? "VALIDATING") ? "text-primary-deep font-semibold" : ""}>
            {STAGE_LABELS[s]}
          </li>
        ))}
      </ol>
      {progress.degraded.length > 0 && (
        <ul className="mt-3 rounded-2xl border border-amber/50 bg-amber/10 p-3 text-xs text-navy">
          {progress.degraded.map((d, i) => (
            <li key={`${d.service}-${i}`}>
              <strong>{d.service}</strong>: {d.reason ?? "limited"}
            </li>
          ))}
        </ul>
      )}
      {progress.status === "NEEDS_INPUT" && (
        <div className="mt-3 rounded-2xl border border-amber/50 bg-amber/10 p-3 text-sm text-navy">
          <p>Please confirm: {progress.missingFields.map((f) => f.replace(".confirmed_by_user", " pin").replace("_", " ")).join(", ")}.</p>
          {onResume && (
            <Button size="sm" className="mt-2" onPress={onResume}>
              I have confirmed — continue
            </Button>
          )}
        </div>
      )}
      {progress.status === "FAILED" && (
        <div className="mt-3 rounded-2xl border border-coral/40 bg-coral/5 p-3 text-sm text-navy">
          <p>
            {progress.error?.message ?? "The assessment could not be completed."} <span className="text-ink-muted">({progress.error?.code})</span>
          </p>
          {progress.error?.retryable && onRetry && (
            <Button size="sm" className="mt-2" onPress={onRetry}>
              Try again
            </Button>
          )}
        </div>
      )}
    </section>
  );
}
