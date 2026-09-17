import { describe, expect, it } from "vitest";

import { sosReducer, SOS_INITIAL } from "@/components/emergency/sosMachine";
import { pickRouteOptions } from "@/components/trip/RouteOptions";
import { isoToLocal, localToIso, tripFormSchema, tzOffsetMinutes } from "@/components/trip/tripSchema";
import { applyEvent, INITIAL_PROGRESS, parseEventData } from "@/features/assessment/useRunEvents";
import { ApiError, buildUrl, toApiError } from "@/lib/api/client";
import type { RecommendationResponse, RouteCandidate } from "@/lib/api/generated/contracts";
import { clampBbox, eventMarkers, geometryPoint } from "@/lib/map";
import { ACTION_LABELS, RISK_LABELS, relativeAge, stagePercent } from "@/lib/utils";

describe("SSE reducer", () => {
  it("applies contract events in order and drops duplicates after reconnect", () => {
    let s = INITIAL_PROGRESS;
    s = applyEvent(s, { event_id: 1, event_type: "run.accepted", request_id: "r", status: "QUEUED", occurred_at: "2026-09-17T00:00:00Z" });
    s = applyEvent(s, { event_id: 2, event_type: "run.progress", request_id: "r", status: "RUNNING", stage: "ASSESSING_RISK", occurred_at: "2026-09-17T00:00:01Z" });
    s = applyEvent(s, { event_id: 2, event_type: "run.progress", request_id: "r", status: "RUNNING", stage: "MAKING_DECISION", occurred_at: "2026-09-17T00:00:01Z" });
    expect(s.stage).toBe("ASSESSING_RISK");
    s = applyEvent(s, { event_id: 3, event_type: "run.degraded", request_id: "r", service: "openrouteservice", reason: "no key", occurred_at: "2026-09-17T00:00:02Z" });
    s = applyEvent(s, { event_id: 4, event_type: "heartbeat", request_id: "r", occurred_at: "2026-09-17T00:00:02Z" });
    s = applyEvent(s, { event_id: 5, event_type: "run.completed", request_id: "r", status: "PARTIAL", recommendation_id: "rec-1", occurred_at: "2026-09-17T00:00:03Z" });
    expect(s.status).toBe("PARTIAL");
    expect(s.recommendationId).toBe("rec-1");
    expect(s.degraded).toHaveLength(1);
    expect(s.percent).toBe(100);
    expect(s.events).toHaveLength(4); // heartbeat not recorded
  });
  it("maps failed and needs_input", () => {
    const failed = applyEvent(INITIAL_PROGRESS, { event_id: 1, event_type: "run.failed", request_id: "r", error_code: "DEPENDENCY_UNAVAILABLE", retryable: true, occurred_at: "2026-09-17T00:00:00Z" });
    expect(failed.status).toBe("FAILED");
    expect(failed.error?.retryable).toBe(true);
    const ni = applyEvent(INITIAL_PROGRESS, { event_id: 1, event_type: "run.needs_input", request_id: "r", missing_fields: ["origin.confirmed_by_user"], occurred_at: "2026-09-17T00:00:00Z" });
    expect(ni.status).toBe("NEEDS_INPUT");
    expect(ni.missingFields).toEqual(["origin.confirmed_by_user"]);
  });
  it("rejects malformed payloads", () => {
    expect(parseEventData("not json")).toBeNull();
    expect(parseEventData(JSON.stringify({ foo: 1 }))).toBeNull();
    expect(parseEventData(JSON.stringify({ event_id: 1, event_type: "run.progress", request_id: "r", occurred_at: "x" }))?.event_type).toBe("run.progress");
  });
  it("stage percent follows the contract order", () => {
    expect(stagePercent("VALIDATING")).toBeLessThan(stagePercent("FORMATTING_RESPONSE"));
    expect(stagePercent("FORMATTING_RESPONSE")).toBe(100);
    expect(stagePercent(null)).toBe(0);
  });
});

describe("API client", () => {
  it("builds proxy URLs with query params and never leaks empty values", () => {
    expect(buildUrl("locations/search", { q: "Chiang Mai", locale: "th", country: undefined })).toBe("/api/backend/locations/search?q=Chiang+Mai&locale=th");
  });
  it("maps the error envelope to a stable ApiError", () => {
    const e = toApiError(429, { error: { code: "RATE_LIMITED", message: "slow down", retryable: true, retry_after_seconds: 7 }, meta: { request_id: "abc" } });
    expect(e).toBeInstanceOf(ApiError);
    expect(e.code).toBe("RATE_LIMITED");
    expect(e.retryAfterSeconds).toBe(7);
    expect(e.requestId).toBe("abc");
    const auth = toApiError(401, null);
    expect(auth.isAuth).toBe(true);
    const v = toApiError(422, { error: { code: "VALIDATION_ERROR", message: "bad", field_errors: [{ path: "departure_time", code: "IN_PAST" }] } });
    expect(v.fieldErrors[0]?.code).toBe("IN_PAST");
    expect(v.retryable).toBe(false);
  });
});

describe("timezone handling", () => {
  it("interprets datetime-local in the trip timezone, not the browser timezone", () => {
    expect(localToIso("2026-10-01T09:30", "Asia/Bangkok")).toBe("2026-10-01T02:30:00.000Z");
    expect(localToIso("2026-10-01T09:30", "America/New_York")).toBe("2026-10-01T13:30:00.000Z");
    expect(localToIso("garbage", "Asia/Bangkok")).toBeNull();
    expect(tzOffsetMinutes(new Date("2026-07-01T00:00:00Z"), "Europe/Paris")).toBe(120);
    expect(isoToLocal("2026-10-01T02:30:00.000Z", "Asia/Bangkok")).toBe("2026-10-01T09:30");
  });
  it("form schema rejects unconfirmed pins, past departures and same origin/destination", () => {
    const loc = (id: string, confirmed: boolean) => ({
      place_id: id,
      display_name: id,
      coordinates: { type: "Point" as const, coordinates: [100.5, 13.7] },
      country_code: "TH",
      timezone: "Asia/Bangkok",
      provider: "open_meteo_geocoding",
      confirmed_by_user: confirmed,
    });
    const base = { travel_modes: ["CAR" as const], prefer_safer_route: true, prefer_lower_emissions: false, prefer_lower_cost: false, timezone: "Asia/Bangkok" };
    const future = new Date(Date.now() + 24 * 3600_000).toISOString().slice(0, 16); // +24h covers any zone offset
    const r1 = tripFormSchema.safeParse({ ...base, origin: loc("a", false), destination: loc("b", true), departureLocal: future });
    expect(r1.success).toBe(false);
    expect(r1.error?.issues.some((i) => i.path[0] === "origin")).toBe(true);
    const r2 = tripFormSchema.safeParse({ ...base, origin: loc("a", true), destination: loc("a", true), departureLocal: future });
    expect(r2.error?.issues.some((i) => i.path[0] === "destination")).toBe(true);
    const r3 = tripFormSchema.safeParse({ ...base, origin: loc("a", true), destination: loc("b", true), departureLocal: "2020-01-01T09:00" });
    expect(r3.error?.issues.some((i) => i.path[0] === "departureLocal")).toBe(true);
    const ok = tripFormSchema.safeParse({ ...base, origin: loc("a", true), destination: loc("b", true), departureLocal: future });
    expect(ok.success).toBe(true);
  });
});

describe("display mappers", () => {
  it("has a label for every action and risk code and never derives them", () => {
    expect(Object.keys(ACTION_LABELS).sort()).toEqual(["AVOID", "CHANGE_ROUTE", "DELAY", "NORMAL"]);
    expect(Object.keys(RISK_LABELS).sort()).toEqual(["HIGH", "LOW", "MEDIUM", "UNKNOWN"]);
    expect(RISK_LABELS.UNKNOWN.tone).toBe("default");
  });
  it("relative age is honest", () => {
    const now = Date.parse("2026-09-17T10:00:00Z");
    expect(relativeAge("2026-09-17T09:52:00Z", now)).toBe("8 min ago");
    expect(relativeAge(null, now)).toBe("unknown");
    expect(relativeAge("2026-09-16T09:00:00Z", now)).toBe("1 d ago");
  });
});

describe("SOS state machine", () => {
  it("requires hold then confirm before anything else", () => {
    let s = SOS_INITIAL;
    s = sosReducer(s, { type: "CONFIRM" });
    expect(s.step).toBe("HOLD");
    s = sosReducer(s, { type: "SHARE_ON" });
    expect(s.sharing).toBe(false);
    s = sosReducer(s, { type: "HOLD_COMPLETE", at: 1 });
    expect(s.step).toBe("CONFIRM");
    s = sosReducer(s, { type: "CANCEL" });
    expect(s).toEqual(SOS_INITIAL);
    s = sosReducer(sosReducer(s, { type: "HOLD_COMPLETE", at: 2 }), { type: "CONFIRM" });
    expect(s.step).toBe("SHARE");
    s = sosReducer(s, { type: "SHARE_ON" });
    expect(s.step).toBe("CONNECT");
    expect(s.sharing).toBe(true);
    s = sosReducer(s, { type: "SHARE_OFF" });
    expect(s.sharing).toBe(false);
  });
});

describe("map helpers", () => {
  it("clamps viewport to the API bbox limit and reads any geometry", () => {
    const b = clampBbox([-100, -60, 100, 60]);
    expect(b[2] - b[0]).toBeLessThanOrEqual(9.9);
    expect(geometryPoint({ type: "Point", coordinates: [1, 2] })).toEqual([1, 2]);
    expect(geometryPoint({ type: "Polygon", coordinates: [[[0, 0], [2, 0], [2, 2], [0, 2]]] })).toEqual([1, 1]);
    const m = eventMarkers([
      {
        event_id: "e1",
        event_type: "STORM",
        title: "Storm",
        severity: "SEVERE",
        geometry: { type: "Point", coordinates: [10, 20] },
        source: { source_id: "s", provider: "gdacs", fetched_at: "2026-09-17T00:00:00Z", content_hash: "h" },
      },
    ]);
    expect(m[0]?.severity).toBe("HIGH");
    expect(m[0]?.label).toContain("SEVERE");
  });
});

describe("route options", () => {
  const route = (id: string, labels: RouteCandidate["labels"]): RouteCandidate => ({
    route_id: id,
    labels,
    mode: "CAR",
    geometry: { type: "LineString", coordinates: [[0, 0], [1, 1]] },
    distance_m: 1000,
    duration_seconds: 600,
  });
  it("never fabricates a card for a missing label", () => {
    const rec = { primary_route: route("p", ["RECOMMENDED"]), alternatives: [route("f", ["FASTEST"])] } as unknown as RecommendationResponse;
    const opts = pickRouteOptions(rec);
    expect(opts.map((o) => o.route?.route_id ?? null)).toEqual(["p", "f", null]);
  });
});
