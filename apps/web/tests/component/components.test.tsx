import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { axe } from "vitest-axe";
import { describe, expect, it, vi } from "vitest";

import { SosHoldButton } from "@/components/emergency/SosHoldButton";
import { AssessmentProgress } from "@/components/trip/AssessmentProgress";
import { RouteOptions } from "@/components/trip/RouteOptions";
import { ToastProvider } from "@/components/ui/toast";
import { ActionBadge, DegradedBanner, ErrorState, RiskBadge } from "@/components/ui/primitives";
import { INITIAL_PROGRESS } from "@/features/assessment/useRunEvents";
import { ApiError } from "@/lib/api/client";
import type { RecommendationResponse } from "@/lib/api/generated/contracts";

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>{ui}</ToastProvider>
    </QueryClientProvider>,
  );
}

describe("badges", () => {
  it("risk badge exposes level as text and data attribute (not colour only)", async () => {
    const { container } = render(<RiskBadge level="HIGH" />);
    expect(screen.getByText(/high risk/i)).toBeInTheDocument();
    expect(container.querySelector("[data-risk='HIGH']")).not.toBeNull();
    expect(await axe(container)).toHaveNoViolations();
  });
  it("action badge renders the server action verbatim", () => {
    render(<ActionBadge action="AVOID" />);
    expect(screen.getByText("Avoid travel")).toHaveAttribute("data-action", "AVOID");
  });
});

describe("degraded and error states", () => {
  it("shows every degraded service and limitation", () => {
    render(<DegradedBanner services={["risk-knowledge: model unavailable (rule baseline)"]} limitations={["UNAVAILABLE_CAPABILITY:openrouteservice"]} />);
    expect(screen.getByRole("status")).toHaveTextContent(/model unavailable/);
    expect(screen.getByRole("status")).toHaveTextContent(/Unavailable: openrouteservice/);
  });
  it("renders nothing when healthy", () => {
    const { container } = render(<DegradedBanner services={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
  it("offers retry only for retryable errors and shows the request reference", () => {
    const retry = vi.fn();
    const err = new ApiError({ code: "DEPENDENCY_TIMEOUT", message: "slow", status: 504, retryable: true, requestId: "abcdef123456" });
    render(<ErrorState error={err} onRetry={retry} />);
    expect(screen.getByRole("alert")).toHaveTextContent(/taking too long/);
    expect(screen.getByRole("alert")).toHaveTextContent(/ref abcdef12/);
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(retry).toHaveBeenCalled();
    const nonRetry = new ApiError({ code: "VALIDATION_ERROR", message: "bad", status: 422 });
    render(<ErrorState error={nonRetry} onRetry={retry} />);
    expect(screen.getAllByRole("button", { name: /retry/i })).toHaveLength(1);
  });
});

describe("SOS hold button", () => {
  it("activates only after a full 3 s hold and cancels on early release", () => {
    vi.useFakeTimers();
    let now = 0;
    vi.spyOn(performance, "now").mockImplementation(() => now);
    const rafs: FrameRequestCallback[] = [];
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
      rafs.push(cb);
      return rafs.length;
    });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => undefined);
    const onActivate = vi.fn();
    render(<SosHoldButton onActivate={onActivate} />);
    const btn = screen.getByRole("button", { name: /hold for sos/i });
    const runFrames = (ms: number) => {
      now += ms;
      const cbs = rafs.splice(0);
      act(() => cbs.forEach((cb) => cb(now)));
    };
    fireEvent.keyDown(btn, { key: " " });
    runFrames(1000);
    fireEvent.keyUp(btn, { key: " " });
    runFrames(3000);
    expect(onActivate).not.toHaveBeenCalled();
    fireEvent.pointerDown(btn, { pointerId: 1 });
    runFrames(1500);
    expect(onActivate).not.toHaveBeenCalled();
    runFrames(1600);
    expect(onActivate).toHaveBeenCalledTimes(1);
    vi.restoreAllMocks();
    vi.useRealTimers();
  });
});

describe("assessment progress", () => {
  it("renders stage labels from the contract and needs-input prompt", () => {
    const onResume = vi.fn();
    render(<AssessmentProgress progress={{ ...INITIAL_PROGRESS, status: "RUNNING", stage: "RETRIEVING_GUIDANCE" }} />);
    expect(screen.getAllByText("Looking up official guidance").length).toBeGreaterThan(0);
    render(<AssessmentProgress progress={{ ...INITIAL_PROGRESS, status: "NEEDS_INPUT", missingFields: ["origin.confirmed_by_user"] }} onResume={onResume} />);
    fireEvent.click(screen.getByRole("button", { name: /continue/i }));
    expect(onResume).toHaveBeenCalled();
  });
});

describe("route options", () => {
  it("shows unavailable cards honestly when the provider is missing", () => {
    const rec = {
      recommendation_id: "r1",
      freshness: { fetched_at: new Date().toISOString() },
      primary_route: {
        route_id: "p",
        labels: ["RECOMMENDED"],
        mode: "CAR",
        geometry: { type: "LineString", coordinates: [[0, 0], [1, 1]] },
        distance_m: 580000,
        duration_seconds: 28800,
        risk_level: "LOW",
      },
      alternatives: [],
    } as unknown as RecommendationResponse;
    wrap(<RouteOptions rec={rec} tripId="t1" unavailable={["UNAVAILABLE_CAPABILITY:openrouteservice: ORS_API_KEY not configured"]} />);
    expect(screen.getByRole("link", { name: /recommended/i })).toHaveAttribute("href", expect.stringContaining("candidate=p"));
    expect(screen.getAllByText(/provider unavailable/i)).toHaveLength(2);
  });
});
