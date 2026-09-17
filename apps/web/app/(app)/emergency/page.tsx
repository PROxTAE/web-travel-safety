"use client";

import { Button } from "@heroui/react";
import { Building2, ChevronRight, Lightbulb, Lock, MapPin, Phone, Plus, Send, ShieldCheck, Siren } from "lucide-react";
import Image from "next/image";
import { useMemo, useReducer, useState } from "react";

import { EmergencyProfilePanel } from "@/components/emergency/EmergencyProfilePanel";
import { SosHoldButton } from "@/components/emergency/SosHoldButton";
import { SOS_INITIAL, SOS_STEPS, sosReducer } from "@/components/emergency/sosMachine";
import { DynamicMap } from "@/components/map/DynamicMap";
import type { MapMarker } from "@/components/map/MapView";
import { PageHeader } from "@/components/shell/PageHeader";
import { LocationSearch } from "@/components/trip/LocationSearch";
import { DataFreshness, ErrorState } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";
import { useMe } from "@/features/auth/useMe";
import { useConsents, useEmergencyContacts, useGeolocation, useGrantConsent, useNearby, type NearbyType } from "@/features/emergency/hooks";
import { useCurrentTrip } from "@/features/trips/hooks";
import { ApiError } from "@/lib/api/client";
import type { LocationRef } from "@/lib/api/generated/contracts";
import { cn } from "@/lib/utils";

const NEARBY: Array<{ type: NearbyType; title: string; text: string; icon: typeof Siren; tone: string }> = [
  { type: "POLICE", title: "Local emergency services", text: "Find nearby police, fire or rescue services based on your location.", icon: Siren, tone: "bg-coral/10 text-coral" },
  { type: "MEDICAL", title: "Medical help", text: "Find nearby hospitals, clinics or urgent care.", icon: Plus, tone: "bg-mint text-primary-deep" },
  { type: "EMBASSY", title: "Embassy or consulate", text: "Find your country's embassy or consulate near you.", icon: Building2, tone: "bg-weather/10 text-weather" },
];

/** Screen 05. Nothing is called or shared before the confirm step and the user's explicit consent. */
export default function EmergencyPage() {
  const [sos, dispatch] = useReducer(sosReducer, SOS_INITIAL);
  const geo = useGeolocation(true);
  const consents = useConsents();
  const grant = useGrantConsent();
  const me = useMe();
  const trips = useCurrentTrip();
  const toast = useToast();
  const [manual, setManual] = useState<LocationRef | null>(null);
  const [nearbyType, setNearbyType] = useState<NearbyType | null>(null);

  const geoState = geo.state;
  const coords = useMemo(
    () =>
      geoState.status === "ready"
        ? { lat: geoState.lat, lon: geoState.lon }
        : manual
          ? { lat: manual.coordinates.coordinates[1]!, lon: manual.coordinates.coordinates[0]! }
          : null,
    [geoState, manual],
  );
  const countryCode = manual?.country_code ?? trips.trip?.destination.country_code ?? null;
  const contacts = useEmergencyContacts(countryCode);
  const nearby = useNearby(nearbyType, coords);
  const hasOnceConsent = consents.data?.some((c) => c.type === "LOCATION_ONCE" && c.granted && !c.revoked_at) ?? false;

  const markers = useMemo<MapMarker[]>(() => {
    const out: MapMarker[] = [];
    if (coords) out.push({ id: "me", kind: "me", lon: coords.lon, lat: coords.lat, label: "Your location" });
    for (const f of nearby.data?.features ?? []) {
      const [lon, lat] = f.geometry.coordinates;
      if (lon !== undefined && lat !== undefined) out.push({ id: String(f.properties.id ?? `${lon},${lat}`), kind: "poi", lon, lat, label: String(f.properties.name ?? nearbyType) });
    }
    return out;
  }, [coords, nearby.data, nearbyType]);

  const shareLocation = async () => {
    try {
      if (!hasOnceConsent) await grant.mutateAsync({ type: "LOCATION_ONCE", granted: true });
      geo.start();
      dispatch({ type: "SHARE_ON" });
    } catch (e) {
      toast.push({ tone: "danger", title: "Could not record consent", description: e instanceof ApiError ? e.message : undefined });
    }
  };
  const stopSharing = () => {
    geo.stop();
    dispatch({ type: "SHARE_OFF" });
  };
  const findNearest = async (type: NearbyType) => {
    if (!coords && geo.state.status !== "denied") {
      if (!hasOnceConsent) await grant.mutateAsync({ type: "LOCATION_ONCE", granted: true }).catch(() => undefined);
      geo.start();
    }
    setNearbyType(type);
  };

  const primary = contacts.data?.contacts.find((c) => c.service_type === "GENERAL_EMERGENCY" || c.service_type === "POLICE");

  return (
    <>
      <PageHeader
        title="Emergency Center"
        subtitle="Get help wherever you are"
        icon={<Siren className="text-coral" aria-hidden />}
        aside={
          <div className="sta-card flex items-center gap-3 px-4 py-3">
            <span className="sta-icon-tile !w-10 !h-10 bg-mint text-primary-deep"><ShieldCheck size={18} aria-hidden /></span>
            <div>
              <p className="font-bold text-navy">You&apos;re not alone.</p>
              <p className="text-xs text-ink-muted">Help is always within reach.</p>
            </div>
          </div>
        }
      />
      <div className="grid gap-4 xl:grid-cols-[1fr_1.2fr_0.8fr]">
        {/* SOS */}
        <section className="sta-card p-5 flex flex-col items-center" aria-labelledby="sos-title">
          <h2 id="sos-title" className="sr-only">SOS</h2>
          {sos.step === "HOLD" && <SosHoldButton onActivate={() => dispatch({ type: "HOLD_COMPLETE", at: Date.now() })} />}
          {sos.step === "CONFIRM" && (
            <div role="alertdialog" aria-labelledby="confirm-title" className="w-full rounded-3xl border border-coral/40 bg-coral/5 p-5 text-center">
              <h3 id="confirm-title" className="text-xl font-extrabold text-navy">Do you need emergency help?</h3>
              <p className="mt-1 text-sm text-ink-muted">Confirming shows local emergency numbers and lets you share your location. No call is placed automatically.</p>
              <div className="mt-4 grid grid-cols-2 gap-2">
                <Button variant="ghost" onPress={() => dispatch({ type: "CANCEL" })}>Cancel</Button>
                <Button variant="danger" onPress={() => dispatch({ type: "CONFIRM" })} autoFocus>Yes, I need help</Button>
              </div>
            </div>
          )}
          {(sos.step === "SHARE" || sos.step === "CONNECT") && (
            <div className="w-full rounded-3xl bg-surface-secondary p-5">
              <p className="font-extrabold text-navy">{sos.step === "SHARE" ? "Share your location?" : "Connect with help"}</p>
              {sos.step === "SHARE" && (
                <>
                  <p className="mt-1 text-sm text-ink-muted">Optional. Your position is used only to find the nearest services and is not stored in a history.</p>
                  <div className="mt-3 grid grid-cols-2 gap-2">
                    <Button variant="outline" onPress={() => dispatch({ type: "SKIP_SHARE" })}>Skip</Button>
                    <Button onPress={shareLocation}><Send size={16} aria-hidden /> Share location</Button>
                  </div>
                </>
              )}
              {sos.step === "CONNECT" && (
                <ul className="mt-3 flex flex-col gap-2">
                  {contacts.isLoading && <li className="h-12 animate-pulse rounded-2xl bg-white" />}
                  {contacts.data?.contacts.slice(0, 4).map((c) => (
                    <li key={`${c.service_type}-${c.phone}`}>
                      {/* tel: opens the dialer only when tapped */}
                      <a href={`tel:${c.phone_e164 ?? c.phone}`} className="flex items-center gap-3 rounded-2xl bg-white p-3 hover:bg-mint">
                        <span className="sta-icon-tile !w-10 !h-10 bg-coral/10 text-coral"><Phone size={18} aria-hidden /></span>
                        <span className="flex-1">
                          <span className="block text-lg font-extrabold text-navy">{c.phone}</span>
                          <span className="block text-xs text-ink-muted">{c.label} · {c.country_code}{c.subdivision ? ` · ${c.subdivision}` : ""}</span>
                        </span>
                        <ChevronRight className="text-ink-muted" aria-hidden />
                      </a>
                    </li>
                  ))}
                  {!contacts.isLoading && !contacts.data?.contacts.length && (
                    <li className="rounded-2xl bg-white p-3 text-sm text-navy">No verified directory for {countryCode ?? "your location"}. Choose a place below to look up the local directory.</li>
                  )}
                  {contacts.data?.limitations?.map((l) => <li key={l} className="text-xs text-amber-ink">{l}</li>)}
                </ul>
              )}
              <Button variant="ghost" size="sm" className="mt-3" onPress={() => { stopSharing(); dispatch({ type: "RESET" }); }}>Done / reset</Button>
            </div>
          )}
          <ol className="mt-6 grid w-full grid-cols-4 gap-1" aria-label="SOS steps">
            {SOS_STEPS.map((s, i) => {
              const idx = SOS_STEPS.findIndex((x) => x.key === sos.step);
              const state = i < idx ? "done" : i === idx ? "current" : "todo";
              return (
                <li key={s.key} className="flex flex-col items-center gap-1 text-center text-xs font-semibold" aria-current={state === "current" ? "step" : undefined}>
                  <span className={cn("flex h-7 w-7 items-center justify-center rounded-full text-white", state === "current" ? "bg-coral" : state === "done" ? "bg-primary" : "bg-line text-ink-muted")}>{i + 1}</span>
                  <span className={state === "todo" ? "text-ink-muted" : "text-navy"}>{s.label}</span>
                </li>
              );
            })}
          </ol>
        </section>

        {/* Live location */}
        <section className="sta-card p-4 flex flex-col" aria-labelledby="live-location">
          <div className="flex items-center justify-between">
            <h2 id="live-location" className="flex items-center gap-2 text-lg font-extrabold text-navy"><MapPin className="text-primary-deep" aria-hidden />Your live location</h2>
            <span className={cn("text-sm font-semibold", geo.state.status === "ready" ? "text-primary-deep" : "text-ink-muted")}>
              ● {geo.state.status === "ready" ? "Location ready" : geo.state.status === "requesting" ? "Requesting…" : geo.state.status === "denied" ? "Permission denied" : "Not shared"}
            </span>
          </div>
          <div className="relative mt-3 h-72 flex-1">
            <DynamicMap ariaLabel="Your location and nearby services" markers={markers} center={coords ? [coords.lon, coords.lat] : undefined} zoom={coords ? 13 : 3} fitTo={coords && markers.length > 1 ? [[Math.min(...markers.map((m) => m.lon)), Math.min(...markers.map((m) => m.lat))], [Math.max(...markers.map((m) => m.lon)), Math.max(...markers.map((m) => m.lat))]] : null} />
            <p className="absolute right-3 top-3 rounded-xl bg-white/95 px-3 py-2 text-xs text-navy shadow-card">Sharing helps get you faster, more accurate help.</p>
          </div>
          {geo.state.status === "ready" ? (
            <Button variant="outline" fullWidth className="mt-3" onPress={stopSharing}>Stop sharing</Button>
          ) : (
            <Button fullWidth size="lg" className="mt-3" isDisabled={geo.state.status === "requesting"} onPress={shareLocation}><Send size={18} aria-hidden /> Share location <ChevronRight size={16} aria-hidden /></Button>
          )}
          <p className="mt-2 flex items-center justify-center gap-1 text-xs text-ink-muted"><Lock size={12} aria-hidden /> We&apos;ll ask for your permission before sharing your location.</p>
          {(geo.state.status === "denied" || geo.state.status === "unavailable") && (
            <div className="mt-3">
              <LocationSearch label="Choose your location manually" value={manual} onChange={setManual} locale={(me.data?.locale ?? "en").split("-")[0]} />
            </div>
          )}
        </section>

        {/* Right column */}
        <div className="flex flex-col gap-4">
          <div className="sta-card flex items-center gap-3 p-4">
            <Image src="/assets/mascot/mascot-emergency-help.png" alt="" width={110} height={110} />
            <p className="rounded-2xl rounded-bl-sm bg-mint px-3 py-2 text-sm text-navy"><strong>Stay calm.</strong><br />I&apos;ll guide you step by step.</p>
          </div>
          <EmergencyProfilePanel />
          <section className="sta-card p-4">
            <h2 className="flex items-center gap-2 font-extrabold text-navy"><Lightbulb className="text-amber" aria-hidden />In an emergency</h2>
            <ul className="mt-2 list-disc pl-5 text-sm text-ink">
              <li>Find a safe place if possible</li>
              <li>Share your location</li>
              <li>Follow local authorities&apos; instructions</li>
              <li>I&apos;ll help you with the next steps</li>
            </ul>
            {primary && <p className="mt-2 text-xs text-ink-muted">Verified {countryCode} emergency number: <strong>{primary.phone}</strong> · <DataFreshness fetchedAt={primary.verified_at} label="verified" maxAgeMinutes={60 * 24 * 90} /></p>}
          </section>
        </div>

        {/* Find nearest */}
        {NEARBY.map((n) => (
          <section key={n.type} className="sta-card p-4 flex flex-col" aria-labelledby={`nearby-${n.type}`}>
            <div className="flex items-start gap-3">
              <span className={cn("sta-icon-tile", n.tone)}><n.icon aria-hidden /></span>
              <div>
                <h2 id={`nearby-${n.type}`} className="font-extrabold text-navy">{n.title}</h2>
                <p className="text-sm text-ink-muted">{n.text}</p>
              </div>
            </div>
            <Button variant="secondary" fullWidth className="mt-3" isDisabled={nearby.isFetching && nearbyType === n.type} onPress={() => findNearest(n.type)}>
              Find nearest <ChevronRight size={16} aria-hidden />
            </Button>
            {nearbyType === n.type && (
              <div className="mt-3 text-sm">
                {!coords && geo.state.status !== "denied" && <p className="text-ink-muted">Waiting for your location…</p>}
                {!coords && geo.state.status === "denied" && <p className="text-amber-ink">Location denied — choose a place manually in the map card.</p>}
                {nearby.isError && <ErrorState error={nearby.error} compact />}
                {nearby.data && (
                  <ul className="flex flex-col gap-1">
                    {nearby.data.features.slice(0, 5).map((f) => (
                      <li key={String(f.properties.id ?? f.geometry.coordinates.join(","))} className="rounded-xl bg-surface-secondary px-3 py-2">
                        <span className="font-semibold text-navy">{String(f.properties.name ?? "Unnamed")}</span>
                        <span className="block text-xs text-ink-muted">{f.properties.distance_m != null ? `${Math.round(Number(f.properties.distance_m))} m` : ""} · source {String(f.properties.provider ?? "provider")}</span>
                      </li>
                    ))}
                    {nearby.data.features.length === 0 && <li className="text-ink-muted">No places returned within 5 km.</li>}
                  </ul>
                )}
              </div>
            )}
          </section>
        ))}
      </div>
    </>
  );
}
