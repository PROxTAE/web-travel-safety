"use client";

import { Button } from "@heroui/react";
import { Bot, ChevronRight, Clock, CloudRain, MapPin, MessageSquare, Paperclip, Plus, Send, Siren, TrainFront, TriangleAlert } from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { use, useEffect, useMemo, useRef, useState } from "react";

import { MessageBubble } from "@/components/assistant/MessageBubble";
import { transportHeadline, weatherHeadline } from "@/components/dashboard/SummaryCards";
import { DynamicMap } from "@/components/map/DynamicMap";
import { AssessmentProgress } from "@/components/trip/AssessmentProgress";
import { ButtonLink, EmptyState, ErrorState, RiskBadge } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";
import { useRunEvents } from "@/features/assessment/useRunEvents";
import { useConversations, useCreateConversation, useMessages, usePostMessage } from "@/features/conversations/hooks";
import { useConsents, useGeolocation, useGrantConsent } from "@/features/emergency/hooks";
import { useCurrentTrip, useRecommendation, useTrip } from "@/features/trips/hooks";
import { ApiError } from "@/lib/api/client";
import { boundsOf, recommendationRoutes, tripMarkers } from "@/lib/map";
import { relativeAge } from "@/lib/utils";

const QUICK = [
  { label: "Check my route", icon: MapPin, text: "Is my route safe right now?", intent: "CHECK_SAFETY" as const },
  { label: "Weather forecast", icon: CloudRain, text: "What is the weather forecast along my route?", intent: "ASK_INFORMATION" as const },
];

/** Screen 04. Every reply is a server run: POST message -> SSE progress -> recommendation blocks. No chain-of-thought. */
export default function AssistantPage({ params }: { params: Promise<{ conversationId: string }> }) {
  const { conversationId } = use(params);
  const router = useRouter();
  const search = useSearchParams();
  const toast = useToast();
  const conversations = useConversations();
  const createConversation = useCreateConversation();
  const isNew = conversationId === "new";
  const messages = useMessages(isNew ? null : conversationId);
  const post = usePostMessage(conversationId);
  const [text, setText] = useState(search.get("q") ?? "");
  const [requestId, setRequestId] = useState<string | null>(null);
  const progress = useRunEvents(requestId);
  const trips = useCurrentTrip();
  const conv = conversations.data?.find((c) => c.id === conversationId);
  const tripId = conv?.trip_id ?? trips.trip?.id ?? null;
  const trip = useTrip(tripId);
  const rec = useRecommendation(trip.data?.latest_recommendation_id);
  const listRef = useRef<HTMLOListElement>(null);

  // "new" => create a conversation bound to the current trip and redirect
  useEffect(() => {
    if (!isNew || createConversation.isPending || trips.isLoading) return;
    createConversation.mutate(
      { trip_id: trips.trip?.id ?? null },
      { onSuccess: (c) => router.replace(`/assistant/${c.id}${text ? `?q=${encodeURIComponent(text)}` : ""}`) },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isNew, trips.isLoading]);

  useEffect(() => {
    if (progress.status === "COMPLETED" || progress.status === "PARTIAL" || progress.status === "FAILED") void messages.refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [progress.status]);

  useEffect(() => {
    listRef.current?.lastElementChild?.scrollIntoView({ block: "end" });
  }, [messages.data?.length, requestId]);

  const send = (t: string, intent?: "CHECK_SAFETY" | "ASK_INFORMATION") => {
    const clean = t.trim();
    if (!clean || post.isPending || isNew) return;
    post.mutate(
      { text: clean, intent_hint: intent },
      {
        onSuccess: (res) => {
          setRequestId(res.run.request_id);
          setText("");
        },
        onError: (e) => {
          const err = e instanceof ApiError ? e : null;
          toast.push({
            tone: "danger",
            title: err?.status === 422 ? "Link this chat to a trip first" : "Message not sent",
            description: err?.message,
          });
        },
      },
    );
  };

  const routes = useMemo(() => (rec.data ? recommendationRoutes(rec.data) : []), [rec.data]);
  const markers = useMemo(() => (trip.data ? tripMarkers(trip.data) : []), [trip.data]);
  const fit = useMemo(() => boundsOf([...routes.flatMap((r) => r.coordinates), ...markers.map((m) => [m.lon, m.lat])]), [routes, markers]);

  return (
    <div className="grid gap-4 xl:grid-cols-[18rem_1fr_20rem]">
      {/* Recent chats */}
      <section className="sta-card p-4 flex flex-col" aria-labelledby="recent-chats">
        <h2 id="recent-chats" className="flex items-center gap-2 text-lg font-extrabold text-navy"><Clock className="text-primary-deep" aria-hidden />Recent chats</h2>
        <ul className="mt-3 flex flex-col gap-2">
          {conversations.isLoading && <li className="h-14 animate-pulse rounded-2xl bg-surface-secondary" />}
          {conversations.data?.slice(0, 8).map((c) => (
            <li key={c.id}>
              <Link href={`/assistant/${c.id}`} aria-current={c.id === conversationId ? "page" : undefined} className={`flex items-center gap-3 rounded-2xl p-3 ${c.id === conversationId ? "bg-mint" : "bg-surface-secondary hover:bg-mint"}`}>
                <span className="sta-icon-tile !w-9 !h-9 bg-white text-primary"><MessageSquare size={16} aria-hidden /></span>
                <span className="flex-1 min-w-0">
                  <span className="block truncate font-bold text-navy">{c.title}</span>
                  <span className="block text-xs text-ink-muted">{relativeAge(c.updated_at)} · {c.message_count} messages</span>
                </span>
                <ChevronRight className="text-ink-muted" aria-hidden />
              </Link>
            </li>
          ))}
          {conversations.data?.length === 0 && <li className="text-sm text-ink-muted">No chats yet.</li>}
        </ul>
        <Button className="mt-4" fullWidth isDisabled={createConversation.isPending} onPress={() => createConversation.mutate({ trip_id: trips.trip?.id ?? null }, { onSuccess: (c) => router.push(`/assistant/${c.id}`) })}>
          <Plus size={18} aria-hidden /> New chat
        </Button>
      </section>

      {/* Chat */}
      <section className="sta-card flex flex-col p-4 min-h-[36rem]" aria-labelledby="chat-title">
        <div className="flex items-center gap-2">
          <h2 id="chat-title" className="text-2xl font-extrabold text-navy">Travel Assistant</h2>
          <span className="text-sm text-primary-deep">● Online</span>
        </div>
        <p className="text-sm text-ink-muted">Your AI companion for a safer, smoother journey</p>
        <ol ref={listRef} className="mt-4 flex flex-1 flex-col gap-4 overflow-y-auto pr-1" aria-live="polite">
          {messages.isError && <ErrorState error={messages.error} onRetry={() => messages.refetch()} compact />}
          {messages.data?.map((m) => (
            <MessageBubble key={m.id} message={m} tripId={tripId} />
          ))}
          {requestId && !messages.data?.some((m) => m.request_id === requestId && m.role === "assistant") && (
            <li className="flex items-end gap-2">
              <Image src="/assets/mascot/mascot-welcome.png" alt="" width={44} height={44} className="rounded-full bg-mint" />
              <div className="max-w-[85%] flex-1"><AssessmentProgress progress={progress} /></div>
            </li>
          )}
          {!isNew && messages.data?.length === 0 && !requestId && (
            <EmptyState title="Ask anything about your trip" description="Safety, weather, transport, or what to do next. Answers use live data for your current trip." />
          )}
        </ol>
        <div className="mt-3 flex flex-wrap gap-2">
          {QUICK.map((q) => (
            <Button key={q.label} variant="outline" size="sm" isDisabled={post.isPending || isNew || !tripId} onPress={() => send(q.text, q.intent)}>
              <q.icon size={14} aria-hidden /> {q.label}
            </Button>
          ))}
          <ButtonLink href="/emergency" variant="outline" size="sm"><Siren size={14} className="text-coral" aria-hidden /> Emergency help</ButtonLink>
        </div>
        <form
          className="mt-3 flex items-center gap-2 rounded-2xl border border-line bg-white px-3 py-2"
          onSubmit={(e) => {
            e.preventDefault();
            send(text);
          }}
        >
          <Paperclip className="text-ink-muted" aria-hidden />
          <input value={text} onChange={(e) => setText(e.target.value)} maxLength={2000} aria-label="Message" placeholder={tripId ? "Ask about your trip, safety, or weather…" : "Plan a trip first so I can use live data"} className="flex-1 bg-transparent outline-none text-navy" disabled={isNew} />
          <button type="submit" aria-label="Send" disabled={post.isPending || isNew || !text.trim()} className="rounded-full bg-primary p-2 text-white disabled:opacity-50"><Send size={18} aria-hidden /></button>
        </form>
        {!tripId && !trips.isLoading && (
          <p className="mt-2 text-xs text-ink-muted">No trip linked. <Link href="/trips/new" className="text-primary-deep underline">Plan a trip</Link> to get live answers.</p>
        )}
      </section>

      {/* Trip context */}
      <aside className="sta-card p-4 flex flex-col gap-3" aria-labelledby="trip-context">
        <h2 id="trip-context" className="flex items-center gap-2 text-lg font-extrabold text-navy"><MapPin className="text-primary-deep" aria-hidden />Trip context</h2>
        <div className="h-40 rounded-2xl overflow-hidden relative">
          {trip.data ? <DynamicMap ariaLabel="Trip context map" routes={routes} markers={markers} fitTo={fit} interactive={false} /> : <Image src="/assets/illustrations/global-map-background.png" alt="" fill className="object-cover" />}
        </div>
        <ContextRow icon={<TriangleAlert aria-hidden />} tone="bg-amber/15 text-amber" label="Risk" value={rec.data ? <RiskBadge level={rec.data.risk_level} size="sm" /> : "No assessment"} detail={rec.data?.short_summary ?? "Run an assessment to see live risk"} />
        <ContextRow icon={<CloudRain aria-hidden />} tone="bg-weather/10 text-weather" label="Weather" value={weatherHeadline(rec.data).value} detail={weatherHeadline(rec.data).detail} />
        <ContextRow icon={<TrainFront aria-hidden />} tone="bg-mint text-primary-deep" label="Transport" value={transportHeadline(rec.data).value} detail={transportHeadline(rec.data).detail} />
        <LiveLocationToggle />
        {tripId && (
          <ButtonLink href={`/trips/${tripId}`} variant="outline" fullWidth><Bot size={16} aria-hidden /> Update my trip</ButtonLink>
        )}
        <Image src="/assets/illustrations/hero-global-travel-banner.png" alt="" width={600} height={200} className="mt-auto rounded-2xl object-cover h-24 w-full" />
      </aside>
    </div>
  );
}

function ContextRow({ icon, tone, label, value, detail }: { icon: React.ReactNode; tone: string; label: string; value: React.ReactNode; detail: string }) {
  return (
    <div className="flex items-center gap-3 rounded-2xl bg-surface-secondary p-3">
      <span className={`sta-icon-tile !w-10 !h-10 ${tone}`}>{icon}</span>
      <span className="flex-1 min-w-0">
        <span className="block font-bold text-navy">{label}: <span className="font-extrabold">{value}</span></span>
        <span className="block truncate text-xs text-ink-muted">{detail}</span>
      </span>
    </div>
  );
}

/** Consent explainer -> browser permission -> POST consent LOCATION_LIVE. Off => stop watching immediately. */
function LiveLocationToggle() {
  const consents = useConsents();
  const grant = useGrantConsent();
  const geo = useGeolocation(true);
  const [explain, setExplain] = useState(false);
  const active = consents.data?.some((c) => c.type === "LOCATION_LIVE" && c.granted && !c.revoked_at) && geo.state.status === "ready";
  const on = Boolean(active) || geo.state.status === "requesting";

  const enable = async () => {
    setExplain(false);
    geo.start();
    await grant.mutateAsync({ type: "LOCATION_LIVE", granted: true });
  };
  const disable = async () => {
    geo.stop();
    await grant.mutateAsync({ type: "LOCATION_LIVE", granted: false });
  };

  return (
    <div className="rounded-2xl border border-line p-3">
      <div className="flex items-center gap-3">
        <span className="sta-icon-tile !w-10 !h-10 bg-mint text-primary-deep"><MapPin aria-hidden /></span>
        <span className="flex-1">
          <span className="block font-bold text-navy">Use live location</span>
          <span className="block text-xs text-ink-muted">Get more accurate and timely advice based on your current location.</span>
        </span>
        <button type="button" role="switch" aria-checked={on} aria-label="Use live location" disabled={grant.isPending} onClick={() => (on ? void disable() : setExplain(true))} className={`relative h-7 w-12 rounded-full transition-colors ${on ? "bg-primary" : "bg-line"} disabled:opacity-50`}>
          <span className={`absolute top-1 h-5 w-5 rounded-full bg-white shadow transition-all ${on ? "left-6" : "left-1"}`} />
        </button>
      </div>
      {geo.state.status === "ready" && <p className="mt-2 text-xs text-primary-deep">Sharing (±{Math.round(geo.state.accuracy)} m) · updated {relativeAge(new Date(geo.state.at).toISOString())}</p>}
      {geo.state.status === "denied" && <p className="mt-2 text-xs text-coral">Location permission was denied in your browser.</p>}
      {explain && (
        <div role="dialog" aria-label="Live location explainer" className="mt-3 rounded-2xl bg-surface-secondary p-3 text-sm text-navy">
          <p>Your browser will ask for permission. Your position is sent only with your questions to improve advice, is not stored in a location history, and stops as soon as you switch this off.</p>
          <div className="mt-2 flex gap-2">
            <Button size="sm" onPress={enable}>Allow</Button>
            <Button size="sm" variant="ghost" onPress={() => setExplain(false)}>Not now</Button>
          </div>
        </div>
      )}
    </div>
  );
}
