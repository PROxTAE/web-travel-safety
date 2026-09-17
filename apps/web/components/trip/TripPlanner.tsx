"use client";

import { Button } from "@heroui/react";
import { MapPin } from "lucide-react";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useCallback, useMemo, useState } from "react";

import { DynamicMap } from "@/components/map/DynamicMap";
import type { MapMarker } from "@/components/map/MapView";
import { RecommendationCard } from "@/components/recommendation/RecommendationCard";
import { AssessmentProgress } from "@/components/trip/AssessmentProgress";
import { RouteOptions } from "@/components/trip/RouteOptions";
import { TripForm, formToInput, tripToForm } from "@/components/trip/TripForm";
import type { TripFormValues } from "@/components/trip/tripSchema";
import { DegradedBanner, ErrorState } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";
import { useMe } from "@/features/auth/useMe";
import { useCancelRun, useResumeRun, useRun, useStartAssessment } from "@/features/assessment/hooks";
import { useRunEvents } from "@/features/assessment/useRunEvents";
import { useCreateTrip, usePatchTrip, useRecommendation } from "@/features/trips/hooks";
import { ApiError } from "@/lib/api/client";
import type { LocationRef, Trip } from "@/lib/api/generated/contracts";
import { boundsOf, recommendationRoutes } from "@/lib/map";

/**
 * Screen 02. Flow: search -> confirm pins on map -> create/patch trip -> POST assessment (Idempotency-Key) ->
 * SSE progress -> route options from the server's recommendation.
 */
export function TripPlanner({ trip }: { trip: Trip | null }) {
  const router = useRouter();
  const toast = useToast();
  const me = useMe();
  const createTrip = useCreateTrip();
  const patchTrip = usePatchTrip(trip?.id ?? "");
  const [tripId, setTripId] = useState<string | null>(trip?.id ?? null);
  const start = useStartAssessment(tripId ?? trip?.id);
  const cancel = useCancelRun();
  const resume = useResumeRun();
  const [requestId, setRequestId] = useState<string | null>(null);
  const progress = useRunEvents(requestId);
  const run = useRun(requestId, progress.status === "DISCONNECTED");
  const recommendationId = progress.recommendationId ?? run.data?.recommendation_id ?? trip?.latest_recommendation_id ?? null;
  const rec = useRecommendation(recommendationId);

  const [pins, setPins] = useState<{ origin: LocationRef | null; destination: LocationRef | null }>({
    origin: trip?.origin ?? null,
    destination: trip?.destination ?? null,
  });
  const [confirmed, setConfirmed] = useState<{ origin: boolean; destination: boolean }>({
    origin: trip?.origin.confirmed_by_user ?? false,
    destination: trip?.destination.confirmed_by_user ?? false,
  });
  const [formKey, setFormKey] = useState(0);
  const [pending, setPending] = useState<TripFormValues | null>(null);

  const onLocationsChange = useCallback((o: LocationRef | null, d: LocationRef | null) => {
    setPins({ origin: o, destination: d });
    setConfirmed({ origin: o?.confirmed_by_user ?? false, destination: d?.confirmed_by_user ?? false });
  }, []);

  const markers = useMemo<MapMarker[]>(() => {
    const out: MapMarker[] = [];
    if (pins.origin) out.push({ id: "o", kind: "origin", lon: pins.origin.coordinates.coordinates[0]!, lat: pins.origin.coordinates.coordinates[1]!, label: pins.origin.display_name });
    if (pins.destination) out.push({ id: "d", kind: "destination", lon: pins.destination.coordinates.coordinates[0]!, lat: pins.destination.coordinates.coordinates[1]!, label: pins.destination.display_name });
    return out;
  }, [pins]);
  const routes = useMemo(() => (rec.data ? recommendationRoutes(rec.data) : []), [rec.data]);
  const fit = useMemo(() => boundsOf([...markers.map((m) => [m.lon, m.lat]), ...routes.flatMap((r) => r.coordinates)]), [markers, routes]);

  const initialForm = useMemo(() => (trip ? tripToForm(trip) : undefined), [trip]);

  const submit = async (values: TripFormValues) => {
    // pins must be confirmed on the map (form schema also enforces it)
    const body = formToInput({
      ...values,
      origin: values.origin ? { ...values.origin, confirmed_by_user: confirmed.origin } : null,
      destination: values.destination ? { ...values.destination, confirmed_by_user: confirmed.destination } : null,
    });
    if (!confirmed.origin || !confirmed.destination) {
      setPending(values);
      toast.push({ tone: "warning", title: "Confirm the pins on the map", description: "Tap “Confirm pin” for both places before searching routes." });
      return;
    }
    try {
      if (trip && tripId) {
        await patchTrip.mutateAsync({ revision: trip.revision, body });
      } else {
        const created = await createTrip.mutateAsync(body);
        setTripId(created.id);
        window.history.replaceState(null, "", `/trips/${created.id}`);
      }
      const ref = await start.mutateAsync({});
      setRequestId(ref.request_id);
    } catch (e) {
      const err = e instanceof ApiError ? e : null;
      if (err?.code === "PRECONDITION_FAILED") {
        toast.push({ tone: "warning", title: "Trip changed elsewhere", description: "Reloading the latest version." });
        router.refresh();
      } else {
        toast.push({ tone: "danger", title: "Could not start the assessment", description: err?.message ?? "Please try again." });
      }
    }
  };

  const confirmPin = (which: "origin" | "destination") => {
    setConfirmed((c) => ({ ...c, [which]: true }));
    setPins((p) => ({ ...p, [which]: p[which] ? { ...p[which]!, confirmed_by_user: true } : null }));
    if (pending) setFormKey((k) => k + 1);
  };

  const busy = createTrip.isPending || patchTrip.isPending || start.isPending;
  const unavailable = (rec.data?.limitations ?? []).filter((l) => l.startsWith("UNAVAILABLE_CAPABILITY"));

  return (
    <div className="grid gap-4 xl:grid-cols-[1fr_1.35fr_1fr]">
      <section className="sta-card p-4">
        <TripForm
          key={formKey}
          initial={pending ? { ...pending, origin: pins.origin, destination: pins.destination } : initialForm}
          onSubmit={submit}
          submitting={busy}
          onLocationsChange={onLocationsChange}
          locale={(me.data?.locale ?? "en-US").split("-")[0]}
        />
      </section>

      <section className="sta-card p-4 flex flex-col min-h-[30rem]" aria-labelledby="route-preview">
        <div className="flex items-center justify-between">
          <div>
            <h2 id="route-preview" className="flex items-center gap-2 text-lg font-extrabold text-navy">
              <MapPin className="text-primary-deep" aria-hidden /> Route Preview
            </h2>
            <p className="text-sm text-ink-muted">
              {pins.origin?.display_name ?? "—"} → {pins.destination?.display_name ?? "—"}
            </p>
          </div>
          <div className="flex gap-2">
            {(["origin", "destination"] as const).map((w) =>
              pins[w] && !confirmed[w] ? (
                <Button key={w} size="sm" onPress={() => confirmPin(w)}>
                  Confirm {w === "origin" ? "departure" : "destination"} pin
                </Button>
              ) : null,
            )}
          </div>
        </div>
        <div className="relative mt-3 flex-1 min-h-96">
          <DynamicMap ariaLabel="Route preview map" markers={markers} routes={routes} fitTo={fit} />
          <div className="absolute bottom-3 left-3 right-3 grid grid-cols-2 gap-1 rounded-xl bg-white/95 px-3 py-2 text-[11px] text-ink-muted shadow-card">
            <p><span className="inline-block w-6 border-t-4 border-dotted border-primary align-middle mr-1" />Recommended route</p>
            <p><span className="inline-block w-6 border-t-4 border-dotted border-[#8b93a7] align-middle mr-1" />Alternative route</p>
            <p>▲ Disruption / hazard marker</p>
            <p>● Origin · ● Destination</p>
          </div>
        </div>
        {requestId && (
          <div className="mt-3">
            <AssessmentProgress
              progress={progress}
              cancelling={cancel.isPending}
              onCancel={() => cancel.mutate(requestId)}
              onRetry={() => start.mutateAsync({}).then((r) => setRequestId(r.request_id))}
              onResume={() => resume.mutateAsync(requestId).then((r) => setRequestId(r.request_id))}
            />
          </div>
        )}
      </section>

      <div className="flex flex-col gap-4">
        {rec.isError ? (
          <ErrorState error={rec.error} onRetry={() => rec.refetch()} compact />
        ) : rec.data && tripId ? (
          <>
            <RouteOptions rec={rec.data} tripId={tripId} unavailable={unavailable} />
            <DegradedBanner services={rec.data.degraded_services} />
            <RecommendationCard rec={rec.data} tripId={tripId} compact />
          </>
        ) : (
          <section className="sta-card p-4">
            <h2 className="text-lg font-extrabold text-navy">Route Options</h2>
            <p className="mt-1 text-sm text-ink-muted">
              {requestId ? "Options appear here as soon as the assessment finishes." : "Fill in your trip and press “Find safe routes”."}
            </p>
          </section>
        )}
        <div className="flex items-end gap-2">
          <p className="rounded-2xl rounded-br-sm bg-white px-4 py-3 text-sm text-navy shadow-card">I&apos;ll check weather, transport, and local risks.</p>
          <Image src="/assets/mascot/mascot-welcome.png" alt="" width={120} height={120} />
        </div>
      </div>
    </div>
  );
}
