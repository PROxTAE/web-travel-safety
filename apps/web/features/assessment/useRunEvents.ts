"use client";

import { useEffect, useRef, useState } from "react";

import { BACKEND_PREFIX } from "@/lib/api/client";
import type { RunProgressEvent, RunStage, RunStatus } from "@/lib/api/generated/contracts";

export type RunProgress = {
  status: RunStatus | "CONNECTING" | "DISCONNECTED";
  stage: RunStage | null;
  percent: number | null;
  messageKey: string | null;
  degraded: Array<{ service: string; reason: string | null }>;
  missingFields: string[];
  recommendationId: string | null;
  error: { code: string; message: string | null; retryable: boolean } | null;
  lastEventId: number;
  events: RunProgressEvent[];
};

export const INITIAL_PROGRESS: RunProgress = {
  status: "CONNECTING",
  stage: null,
  percent: null,
  messageKey: null,
  degraded: [],
  missingFields: [],
  recommendationId: null,
  error: null,
  lastEventId: -1,
  events: [],
};

const TERMINAL: ReadonlySet<string> = new Set(["run.completed", "run.failed"]);
const EVENT_TYPES = ["run.accepted", "run.progress", "run.needs_input", "run.degraded", "run.completed", "run.failed"] as const;

/** Pure reducer (unit-tested): applies one contract event to the progress state. */
export function applyEvent(prev: RunProgress, ev: RunProgressEvent): RunProgress {
  if (ev.event_type === "heartbeat") return prev;
  if (ev.event_id <= prev.lastEventId) return prev; // duplicates after reconnect
  const next: RunProgress = { ...prev, lastEventId: ev.event_id, events: [...prev.events, ev] };
  switch (ev.event_type) {
    case "run.accepted":
      next.status = ev.status ?? "QUEUED";
      break;
    case "run.progress":
      next.status = ev.status ?? "RUNNING";
      next.stage = ev.stage ?? prev.stage;
      next.percent = ev.percent ?? null;
      next.messageKey = ev.message_key ?? null;
      break;
    case "run.degraded":
      next.degraded = [...prev.degraded, { service: ev.service ?? "unknown", reason: ev.reason ?? null }];
      break;
    case "run.needs_input":
      next.status = "NEEDS_INPUT";
      next.missingFields = ev.missing_fields ?? [];
      break;
    case "run.completed":
      next.status = ev.status ?? "COMPLETED";
      next.recommendationId = ev.recommendation_id ?? null;
      next.percent = 100;
      break;
    case "run.failed":
      next.status = "FAILED";
      next.error = {
        code: ev.error_code ?? "INTERNAL_ERROR",
        message: ev.error_message ?? null,
        retryable: ev.retryable ?? false,
      };
      break;
  }
  return next;
}

export function parseEventData(raw: string): RunProgressEvent | null {
  try {
    const parsed = JSON.parse(raw) as RunProgressEvent;
    return typeof parsed.event_id === "number" && typeof parsed.event_type === "string" ? parsed : null;
  } catch {
    return null;
  }
}

/**
 * Subscribes to /runs/{id}/events through the same-origin proxy (cookie auth). The browser's EventSource handles
 * reconnect + Last-Event-ID; the reducer drops duplicates. Closes on terminal events and on unmount.
 */
export function useRunEvents(requestId: string | null | undefined): RunProgress {
  const [state, setState] = useState<{ id: string | null; progress: RunProgress }>({
    id: requestId ?? null,
    progress: INITIAL_PROGRESS,
  });
  const sourceRef = useRef<EventSource | null>(null);
  // a new request id resets the reducer state without an effect (React "adjust state during render" pattern)
  if (state.id !== (requestId ?? null)) setState({ id: requestId ?? null, progress: INITIAL_PROGRESS });

  useEffect(() => {
    if (!requestId || typeof EventSource === "undefined") return;
    const es = new EventSource(`${BACKEND_PREFIX}/runs/${requestId}/events`, { withCredentials: true });
    sourceRef.current = es;
    const handler = (e: Event) => {
      const ev = parseEventData((e as MessageEvent<string>).data);
      if (!ev) return;
      setState((prev) => ({ ...prev, progress: applyEvent(prev.progress, ev) }));
      if (TERMINAL.has(ev.event_type)) es.close();
    };
    for (const type of EVENT_TYPES) es.addEventListener(type, handler);
    es.onerror = () => {
      setState((prev) =>
        TERMINAL.has(prev.progress.events.at(-1)?.event_type ?? "")
          ? prev
          : { ...prev, progress: { ...prev.progress, status: "DISCONNECTED" } },
      );
    };
    es.onopen = () =>
      setState((prev) =>
        prev.progress.status === "DISCONNECTED" || prev.progress.status === "CONNECTING"
          ? { ...prev, progress: { ...prev.progress, status: "QUEUED" } }
          : prev,
      );
    return () => {
      es.close();
      sourceRef.current = null;
    };
  }, [requestId]);

  return state.progress;
}
