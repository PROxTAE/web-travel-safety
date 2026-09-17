/** Typed wrappers over the public API (contract types are generated; see lib/api/generated). */
import { api, type ApiResult } from "@/lib/api/client";
import type {
  AlertSubscription,
  ConsentRecord,
  ConsentType,
  Conversation,
  ConversationMessage,
  DeliveryChannel,
  EmergencyProfile,
  FeedbackCategory,
  FeedbackEvent,
  Intent,
  LocationRef,
  OfficialContact,
  RecommendationResponse,
  RunRef,
  Severity,
  TravelMode,
  TravelPreference,
  Trip,
  UserProfile,
} from "@/lib/api/generated/contracts";

export type RunView = {
  request_id: string;
  trip_id: string;
  conversation_id: string | null;
  kind: "ASSESSMENT" | "FOLLOW_UP" | "REASSESSMENT";
  status: "CREATED" | "QUEUED" | "RUNNING" | "NEEDS_INPUT" | "COMPLETED" | "PARTIAL" | "FAILED" | "CANCELLED";
  stage: string | null;
  missing_fields: string[];
  degraded_services: string[];
  error_code: string | null;
  recommendation_id: string | null;
  result_url: string | null;
  events_url: string;
  poll_url: string;
  submitted_at: string;
  updated_at: string;
  completed_at: string | null;
};

export type TripInput = {
  origin: LocationRef;
  destination: LocationRef;
  departure_time: string;
  return_time?: string | null;
  travel_modes: TravelMode[];
  preferences?: TravelPreference;
  timezone: string;
};

export type SafetyEvents = {
  bbox: number[] | null;
  layers: { disasters?: unknown[]; alerts?: unknown[] };
};

export type EmergencyContactsResult = { contacts: OfficialContact[]; limitations: string[]; directory_version: string };

export type GeoJsonFeatureCollection = {
  type: "FeatureCollection";
  features: Array<{
    type: "Feature";
    geometry: { type: "Point"; coordinates: number[] };
    properties: Record<string, unknown>;
  }>;
};

export const endpoints = {
  me: () => api.get<UserProfile>("me"),
  patchMe: (body: { locale?: string; timezone?: string; display_name?: string | null }) => api.patch<UserProfile>("me", body),
  consents: () => api.get<ConsentRecord[]>("consents"),
  grantConsent: (type: ConsentType, granted: boolean, policy_version = "2026-09") =>
    api.post<ConsentRecord>("consents", { type, granted, policy_version }),
  emergencyProfile: () => api.get<EmergencyProfile>("me/emergency-profile"),
  putEmergencyProfile: (body: Omit<EmergencyProfile, "updated_at">) =>
    api.put<{ stored: boolean; updated_at: string }>("me/emergency-profile", body),

  searchLocations: (q: string, locale: string, signal?: AbortSignal) =>
    api.get<LocationRef[]>("locations/search", { query: { q, locale, limit: 8 }, signal }),

  trips: () => api.get<Trip[]>("trips"),
  trip: (id: string) => api.get<Trip>(`trips/${id}`),
  createTrip: (body: TripInput) => api.post<Trip>("trips", body),
  patchTrip: (id: string, revision: number, body: Partial<TripInput> & { risk_acknowledged?: boolean; status?: string }) =>
    api.patch<Trip>(`trips/${id}`, body, { headers: { "if-match": `W/"${revision}"` } }),
  deleteTrip: (id: string) => api.delete<{ deletion_status: string }>(`trips/${id}`),
  createAssessment: (tripId: string, idempotencyKey: string, body?: { question?: string; intent_hint?: Intent }) =>
    api.post<RunRef>(`trips/${tripId}/assessments`, body ?? {}, { headers: { "idempotency-key": idempotencyKey } }),
  applyRoute: (
    tripId: string,
    revision: number,
    idempotencyKey: string,
    body: { route_id: string; recommendation_id: string; acknowledge_risk?: boolean },
  ): Promise<ApiResult<{ trip: Trip; run: RunRef }>> =>
    api.post(`trips/${tripId}/apply-route`, body, {
      headers: { "if-match": `W/"${revision}"`, "idempotency-key": idempotencyKey },
    }),

  run: (id: string) => api.get<RunView>(`runs/${id}`),
  cancelRun: (id: string) => api.post<RunView>(`runs/${id}/cancel`, { reason: "user_cancelled" }),
  resumeRun: (id: string, idempotencyKey: string) =>
    api.post<RunRef>(`runs/${id}/resume`, undefined, { headers: { "idempotency-key": idempotencyKey } }),
  recommendation: (id: string) => api.get<RecommendationResponse>(`recommendations/${id}`),

  conversations: () => api.get<Conversation[]>("conversations"),
  createConversation: (trip_id?: string | null, title?: string) => api.post<Conversation>("conversations", { trip_id, title }),
  messages: (conversationId: string) => api.get<ConversationMessage[]>(`conversations/${conversationId}/messages`),
  postMessage: (conversationId: string, text: string, intent_hint?: Intent) =>
    api.post<{ message: ConversationMessage; run: RunRef }>(`conversations/${conversationId}/messages`, { text, intent_hint }),

  safetyEvents: (bbox: string, lookbackDays: number, layers: string, signal?: AbortSignal) =>
    api.get<SafetyEvents>("safety/events", { query: { bbox, lookback_days: lookbackDays, layers }, signal }),

  emergencyContacts: (country_code: string, subdivision?: string) =>
    api.get<EmergencyContactsResult>("emergency/contacts", { query: { country_code, subdivision } }),
  emergencyNearby: (type: "POLICE" | "MEDICAL" | "EMBASSY" | "FIRE", lat: number, lon: number) =>
    api.get<GeoJsonFeatureCollection>("emergency/nearby", { query: { type, lat, lon, radius_m: 5000, limit: 10 } }),

  feedback: (recommendation_id: string, category: FeedbackCategory, text?: string) =>
    api.post<FeedbackEvent>("feedback", { recommendation_id, category, text }),
  subscribe: (body: {
    trip_id: string;
    channel: DeliveryChannel;
    consent_id: string;
    severity_threshold?: Severity;
    push_subscription?: Record<string, unknown>;
    email?: string;
  }) => api.post<AlertSubscription>("alert-subscriptions", body),
  unsubscribe: (id: string) => api.delete<void>(`alert-subscriptions/${id}`),
};
