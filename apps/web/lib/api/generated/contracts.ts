// GENERATED FILE — do not edit. Source: packages/contracts/sta_contracts (run `make contracts-generate`).

export const CONTRACT_VERSION = "1.0.0" as const;

export type ActionCode = "NORMAL" | "CHANGE_ROUTE" | "DELAY" | "AVOID";
export const ActionCodeValues = ["NORMAL", "CHANGE_ROUTE", "DELAY", "AVOID"] as const;

export interface AgentRunRequest {
  travel_request: TravelRequest;
  conversation_id?: string | null;
  resume_from_request_id?: string | null;
  budget_override?: Record<string, number> | null;
}

export interface AlertItem {
  alert_id: string;
  title: string;
  severity: Severity;
  event_type: DisasterEventType;
  official: boolean;
  area?: string | null;
  effective_at?: string | null;
  ends_at?: string | null;
  source_url?: string | null;
  observed_at?: string | null;
  fetched_at?: string | null;
}

export interface AlertSubscription {
  id: string;
  trip_id: string;
  channel: DeliveryChannel;
  consent_id: string;
  status: "ACTIVE" | "PAUSED" | "REVOKED";
  severity_threshold?: Severity;
  locale?: string;
  cooldown_until?: string | null;
  created_at: string;
  revoked_at?: string | null;
}

/** [min_lon, min_lat, max_lon, max_lat] */
export interface BBox {
  min_lon: number;
  min_lat: number;
  max_lon: number;
  max_lat: number;
}

export interface ConflictSummary {
  count?: number;
  safety_critical?: number;
  conflicts?: DataConflict[];
}

export interface ConsentRecord {
  id: string;
  type: ConsentType;
  granted: boolean;
  policy_version: string;
  granted_at?: string | null;
  revoked_at?: string | null;
  expires_at?: string | null;
}

export type ConsentType = "LOCATION_ONCE" | "LOCATION_LIVE" | "ALERT_NOTIFICATION" | "ANALYTICS" | "EMERGENCY_PROFILE";
export const ConsentTypeValues = ["LOCATION_ONCE", "LOCATION_LIVE", "ALERT_NOTIFICATION", "ANALYTICS", "EMERGENCY_PROFILE"] as const;

/** Input to external-data /internal/v1/context/query. */
export interface ContextQuery {
  request_id: string;
  trip_id: string;
  origin: LocationRef;
  destination: LocationRef;
  departure_time: string;
  travel_modes: TravelMode[];
  preferences?: TravelPreference;
  locale?: string;
  avoid_geometries?: Polygon[];
  max_weather_samples?: number;
  include?: Array<"weather" | "routes" | "transport" | "disasters">;
}

export interface Conversation {
  id: string;
  trip_id?: string | null;
  title: string;
  last_request_id?: string | null;
  message_count?: number;
  created_at: string;
  updated_at: string;
}

export interface ConversationMessage {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | "system";
  text?: string | null;
  request_id?: string | null;
  recommendation_id?: string | null;
  created_at: string;
}

export interface DataConflict {
  field_path: string;
  values: unknown[];
  source_ids: string[];
  resolution?: string | null;
}

export interface DataQuality {
  status?: DataStatus;
  score?: number;
  flags?: QualityFlag[];
  coverage?: number | null;
  completeness?: number | null;
  freshness_seconds?: number | null;
  conflicts?: DataConflict[];
  notes?: string[];
  weights_version?: string;
}

export type DataStatus = "FRESH" | "STALE" | "UNAVAILABLE" | "CONFLICTING" | "PARTIAL";
export const DataStatusValues = ["FRESH", "STALE", "UNAVAILABLE", "CONFLICTING", "PARTIAL"] as const;

export interface DecisionRequest {
  travel_request: TravelRequest;
  evidence_package: EvidencePackage;
  locale?: string;
  llm_enabled?: boolean;
}

export interface DecisionResult {
  decision_id: string;
  request_id: string;
  snapshot_id: string;
  action_code: ActionCode;
  risk_level: RiskLevel;
  confidence: number;
  selected_route_id?: string | null;
  rules_fired?: string[];
  reason_codes?: ReasonCode[];
  escalation_required?: boolean;
  escalation_reasons?: string[];
  summary: string;
  reasons?: string[];
  immediate_actions?: string[];
  citations?: string[];
  limitations?: string[];
  suggested_delay_minutes?: number | null;
  versions: VersionInfo;
  validation?: DecisionValidation;
  locale?: string;
  created_at: string;
}

/** Wire field ``schema`` is exposed via alias because ``schema`` shadows a BaseModel attribute. */
export interface DecisionValidation {
  schema?: boolean;
  citations?: boolean;
  locked_action?: boolean;
  numbers?: boolean;
  banned_phrases?: boolean;
  used_fallback?: boolean;
  fallback_reason?: string | null;
}

export type DeliveryChannel = "IN_APP" | "EMAIL" | "SMS" | "PUSH";
export const DeliveryChannelValues = ["IN_APP", "EMAIL", "SMS", "PUSH"] as const;

export interface DisasterEvent {
  event_id: string;
  event_type: DisasterEventType;
  title: string;
  description?: string | null;
  severity?: Severity;
  magnitude?: number | null;
  geometry: Point | LineString | Polygon | MultiPolygon;
  effective_at?: string | null;
  ends_at?: string | null;
  updated_at?: string | null;
  instruction?: string | null;
  official?: boolean;
  /** Official closure / no-go; hard constraint for routes */
  closure?: boolean;
  country_codes?: string[];
  quality?: DataQuality;
  source: SourceProvenance;
}

export type DisasterEventType = "EARTHQUAKE" | "CYCLONE" | "STORM" | "FLOOD" | "WILDFIRE" | "VOLCANO" | "LANDSLIDE" | "EXTREME_TEMPERATURE" | "HEALTH" | "TRANSPORT_CLOSURE" | "OTHER";
export const DisasterEventTypeValues = ["EARTHQUAKE", "CYCLONE", "STORM", "FLOOD", "WILDFIRE", "VOLCANO", "LANDSLIDE", "EXTREME_TEMPERATURE", "HEALTH", "TRANSPORT_CLOSURE", "OTHER"] as const;

export interface EmergencyContactPerson {
  name: string;
  relationship?: string | null;
  phone: string;
}

export interface EmergencyInstruction {
  step: number;
  text: string;
  citation_id?: string | null;
}

export interface EmergencyProfile {
  blood_type?: string | null;
  allergies?: string[];
  medications?: string[];
  medical_notes?: string | null;
  contacts?: EmergencyContactPerson[];
  insurance_provider?: string | null;
  insurance_policy_ref?: string | null;
  insurance_phone?: string | null;
  updated_at?: string | null;
}

export interface EvidencePackage {
  package_id: string;
  request_id: string;
  snapshot_id: string;
  trip_revision?: number;
  risk_assessments: RiskAssessment[];
  routes: RouteCandidate[];
  evidence: RetrievedEvidence[];
  quality_summary: QualitySummary;
  conflict_summary: ConflictSummary;
  official_alerts: DisasterEvent[];
  weather_facts?: string[];
  transport_facts?: string[];
  disaster_facts?: string[];
  limitations?: string[];
  degraded_services?: string[];
  knowledge_collection_version?: string | null;
  created_at: string;
}

export interface EvidencePackageRequest {
  snapshot: IntegratedTravelContext;
  locale?: string;
  question?: string | null;
  /** destination country for knowledge geography filter */
  country_code?: string | null;
  /** request the package is built for when a fresh-enough snapshot from an earlier request in the same conversation is reused (follow-up); defaults to snapshot.request_id */
  request_id?: string | null;
}

/** Output of external-data /internal/v1/context/query — all canonical records + provider health. */
export interface ExternalContext {
  context_id: string;
  request_id: string;
  routes: RouteCandidate[];
  weather: WeatherForecastPoint[];
  transport: TransportStatus[];
  disaster_events: DisasterEvent[];
  official_alerts: DisasterEvent[];
  provider_health: ProviderHealth[];
  degraded_services?: string[];
  unavailable_capabilities?: string[];
  fetched_at: string;
  bbox?: BBox | null;
}

export type FeedbackCategory = "HELPFUL" | "INCORRECT" | "STALE" | "UNSAFE" | "ROUTE_ISSUE" | "SOURCE_ISSUE" | "OTHER";
export const FeedbackCategoryValues = ["HELPFUL", "INCORRECT", "STALE", "UNSAFE", "ROUTE_ISSUE", "SOURCE_ISSUE", "OTHER"] as const;

export interface FeedbackEvent {
  id: string;
  recommendation_id: string;
  category: FeedbackCategory;
  text_redacted?: string | null;
  review_status?: "NONE" | "QUEUED" | "IN_REVIEW" | "RESOLVED";
  safety_review_id?: string | null;
  created_at: string;
}

export interface Freshness {
  observed_at?: string | null;
  fetched_at: string;
  expires_at?: string | null;
}

export interface IntegratedTravelContext {
  snapshot_id: string;
  request_id: string;
  trip_id: string;
  trip_revision?: number;
  schema_version?: string;
  feature_schema_version?: string;
  transform_version?: string;
  travel_window: TravelWindow;
  travel_modes: TravelMode[];
  route_candidates: RouteCandidate[];
  route_corridor_geojson?: Polygon | MultiPolygon | null;
  corridor_buffer_m?: number | null;
  weather: WeatherForecastPoint[];
  transport: TransportStatus[];
  disaster_events: DisasterEvent[];
  official_alerts: DisasterEvent[];
  features: Record<string, number | number | null>;
  /** same schema computed on DELAYED probe weather */
  features_delayed?: Record<string, number | number | null> | null;
  delay_probe_minutes?: number | null;
  quality_summary: QualitySummary;
  conflict_summary: ConflictSummary;
  source_ids: string[];
  provider_health?: Record<string, string>;
  created_at: string;
  content_hash: string;
  supersedes_snapshot_id?: string | null;
}

export type Intent = "PLAN_TRIP" | "CHECK_SAFETY" | "ASK_INFORMATION" | "FOLLOW_UP" | "EMERGENCY";
export const IntentValues = ["PLAN_TRIP", "CHECK_SAFETY", "ASK_INFORMATION", "FOLLOW_UP", "EMERGENCY"] as const;

export interface LineString {
  type?: "LineString";
  coordinates: number[][];
}

export interface LocationRef {
  place_id: string;
  display_name: string;
  coordinates: Point;
  country_code: string;
  admin1?: string | null;
  timezone: string;
  provider: string;
  confirmed_by_user?: boolean;
}

export interface ModelRef {
  name: string;
  version: string;
  feature_schema_version: string;
  thresholds_version?: string;
  checksum?: string | null;
}

export interface MultiPolygon {
  type?: "MultiPolygon";
  coordinates: number[][][][];
}

export interface OfficialContact {
  country_code: string;
  subdivision?: string | null;
  service_type: "POLICE" | "MEDICAL" | "FIRE" | "TOURIST_POLICE" | "GENERAL_EMERGENCY" | "EMBASSY" | "DISASTER";
  label: string;
  phone: string;
  phone_e164?: string | null;
  website?: string | null;
  availability?: string | null;
  source_url: string;
  authority: SourceAuthority;
  effective_at: string;
  verified_at: string;
  review_due_at?: string | null;
}

export interface Point {
  type?: "Point";
  coordinates: number[];
}

export interface Polygon {
  type?: "Polygon";
  coordinates: number[][][];
}

export interface ProviderHealth {
  provider: string;
  kind: string;
  status: "UP" | "DEGRADED" | "DOWN" | "UNAVAILABLE" | "UNKNOWN";
  enabled: boolean;
  latency_ms?: number | null;
  quota_remaining?: number | null;
  coverage_note?: string | null;
  checked_at: string;
  last_error_code?: string | null;
}

export type ProviderKind = "GEOCODING" | "WEATHER" | "ROUTE" | "FLIGHT" | "TRANSIT" | "DISASTER" | "EMERGENCY_DIRECTORY" | "PLACES";
export const ProviderKindValues = ["GEOCODING", "WEATHER", "ROUTE", "FLIGHT", "TRANSIT", "DISASTER", "EMERGENCY_DIRECTORY", "PLACES"] as const;

export type QualityFlag = "MISSING" | "STALE" | "CONFLICTING" | "INFERRED" | "INCOMPLETE" | "OUTSIDE_COVERAGE";
export const QualityFlagValues = ["MISSING", "STALE", "CONFLICTING", "INFERRED", "INCOMPLETE", "OUTSIDE_COVERAGE"] as const;

export type QualityGate = "PASS" | "DEGRADED" | "BLOCK";
export const QualityGateValues = ["PASS", "DEGRADED", "BLOCK"] as const;

export interface QualitySummary {
  gate: QualityGate;
  overall: DataQuality;
  weather: DataQuality;
  transport: DataQuality;
  disaster: DataQuality;
  route: DataQuality;
  degraded_services?: string[];
  reasons?: string[];
}

/** Controlled vocabulary for risk/decision reasons (LLM may only paraphrase these). */
export type ReasonCode = "SEVERE_WEATHER_CORRIDOR" | "HEAVY_PRECIPITATION" | "HIGH_WIND" | "LOW_VISIBILITY" | "EXTREME_TEMPERATURE" | "EARTHQUAKE_NEAR_CORRIDOR" | "ACTIVE_DISASTER_EVENT" | "OFFICIAL_ALERT_ACTIVE" | "OFFICIAL_CLOSURE" | "TRANSPORT_DISRUPTION" | "TRANSPORT_DELAY" | "SAFER_ROUTE_AVAILABLE" | "NO_SAFER_ROUTE" | "RISK_DECREASES_LATER" | "LOW_DATA_COVERAGE" | "STALE_DATA" | "CONFLICTING_SOURCES" | "INSUFFICIENT_EVIDENCE" | "NO_RELIABLE_KNOWLEDGE_EVIDENCE" | "MODEL_UNAVAILABLE" | "PROVIDER_UNAVAILABLE" | "CONDITIONS_NORMAL";
export const ReasonCodeValues = ["SEVERE_WEATHER_CORRIDOR", "HEAVY_PRECIPITATION", "HIGH_WIND", "LOW_VISIBILITY", "EXTREME_TEMPERATURE", "EARTHQUAKE_NEAR_CORRIDOR", "ACTIVE_DISASTER_EVENT", "OFFICIAL_ALERT_ACTIVE", "OFFICIAL_CLOSURE", "TRANSPORT_DISRUPTION", "TRANSPORT_DELAY", "SAFER_ROUTE_AVAILABLE", "NO_SAFER_ROUTE", "RISK_DECREASES_LATER", "LOW_DATA_COVERAGE", "STALE_DATA", "CONFLICTING_SOURCES", "INSUFFICIENT_EVIDENCE", "NO_RELIABLE_KNOWLEDGE_EVIDENCE", "MODEL_UNAVAILABLE", "PROVIDER_UNAVAILABLE", "CONDITIONS_NORMAL"] as const;

export interface RecommendationCreateRequest {
  travel_request: TravelRequest;
  decision: DecisionResult;
  evidence_package: EvidencePackage;
  snapshot_created_at: string;
  snapshot_sources: SourceProvenance[];
  weather_summary?: Record<string, unknown> | null;
  transport_summary?: Record<string, unknown> | null;
  previous_recommendation_id?: string | null;
}

export interface RecommendationResponse {
  recommendation_id: string;
  request_id: string;
  trip_id: string;
  conversation_id?: string | null;
  decision_id: string;
  snapshot_id: string;
  status: RunStatus;
  action_code: ActionCode;
  risk_level: RiskLevel;
  confidence: number;
  short_summary: string;
  immediate_actions?: string[];
  reasons?: string[];
  reason_codes?: ReasonCode[];
  primary_route?: RouteCandidate | null;
  alternatives?: RouteCandidate[];
  alerts?: AlertItem[];
  emergency_instructions?: EmergencyInstruction[];
  official_contacts?: OfficialContact[];
  sources?: SourceProvenance[];
  evidence?: RetrievedEvidence[];
  weather_summary?: Record<string, unknown> | null;
  transport_summary?: Record<string, unknown> | null;
  freshness: Freshness;
  limitations?: string[];
  degraded_services?: string[];
  escalation_required?: boolean;
  versions: VersionInfo;
  locale?: string;
  expires_at?: string | null;
  supersedes_recommendation_id?: string | null;
  created_at: string;
}

export interface RetrievedEvidence {
  evidence_id: string;
  document_id: string;
  authority: SourceAuthority;
  title: string;
  source_url: string;
  page?: number | null;
  section?: string | null;
  language: string;
  hazards?: DisasterEventType[];
  effective_at?: string | null;
  expires_at?: string | null;
  passage: string;
  retrieval_score: number;
  rerank_score?: number | null;
  collection_version: string;
  content_hash: string;
}

export interface RiskAssessment {
  assessment_id: string;
  snapshot_id: string;
  route_id: string;
  score: number;
  probability_high: number;
  risk_level: RiskLevel;
  uncertainty: number;
  reason_codes?: ReasonCode[];
  safety_overrides?: SafetyOverride[];
  feature_contributions?: Record<string, number>;
  time_dependent?: boolean;
  later_window_risk_level?: RiskLevel | null;
  later_window_delay_minutes?: number | null;
  model: ModelRef;
  quality?: DataQuality;
  created_at: string;
}

export type RiskLevel = "LOW" | "MEDIUM" | "HIGH" | "UNKNOWN";
export const RiskLevelValues = ["LOW", "MEDIUM", "HIGH", "UNKNOWN"] as const;

export interface RouteCandidate {
  route_id: string;
  provider_route_id?: string | null;
  label?: RouteLabel;
  /** A route may honestly hold several labels */
  labels?: RouteLabel[];
  mode: TravelMode;
  geometry: LineString;
  segments?: RouteSegment[];
  distance_m: number;
  duration_seconds: number;
  transfers?: number;
  exposure?: RouteExposure;
  risk_level?: RiskLevel;
  risk_score?: number | null;
  rank?: number | null;
  usable?: boolean;
  trade_offs?: string[];
  quality?: DataQuality;
  sources?: SourceProvenance[];
}

export interface RouteExposure {
  score?: number;
  hazard_event_ids?: string[];
  weather_window_ids?: string[];
  closed?: boolean;
  severe_weather_minutes?: number;
  max_hazard_severity?: Severity;
  min_hazard_distance_km?: number | null;
}

export type RouteLabel = "ORIGINAL" | "RECOMMENDED" | "FASTEST" | "LOWEST_RISK" | "ALTERNATIVE";
export const RouteLabelValues = ["ORIGINAL", "RECOMMENDED", "FASTEST", "LOWEST_RISK", "ALTERNATIVE"] as const;

export interface RouteSegment {
  index: number;
  mode: TravelMode;
  geometry: LineString;
  distance_m: number;
  duration_seconds: number;
  eta_start?: string | null;
  eta_end?: string | null;
  instruction?: string | null;
  operator?: string | null;
  service_number?: string | null;
}

/** Payload of an SSE event. No prompts, raw provider bodies, coordinates or PII. */
export interface RunProgressEvent {
  event_id: number;
  event_type: "run.accepted" | "run.progress" | "run.needs_input" | "run.degraded" | "run.completed" | "run.failed" | "heartbeat";
  request_id: string;
  status?: RunStatus | null;
  stage?: RunStage | null;
  percent?: number | null;
  message_key?: string | null;
  service?: string | null;
  reason?: string | null;
  retrying?: boolean | null;
  missing_fields?: string[] | null;
  prompt_key?: string | null;
  recommendation_id?: string | null;
  result_url?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  retryable?: boolean | null;
  occurred_at: string;
}

export interface RunRef {
  request_id: string;
  status: RunStatus;
  events_url: string;
  poll_url: string;
  submitted_at: string;
  recommendation_id?: string | null;
  conversation_id?: string | null;
}

export type RunStage = "VALIDATING" | "FETCHING_EXTERNAL_DATA" | "INTEGRATING_DATA" | "ASSESSING_RISK" | "RETRIEVING_GUIDANCE" | "EVALUATING_ROUTES" | "MAKING_DECISION" | "EXPLAINING" | "FORMATTING_RESPONSE";
export const RunStageValues = ["VALIDATING", "FETCHING_EXTERNAL_DATA", "INTEGRATING_DATA", "ASSESSING_RISK", "RETRIEVING_GUIDANCE", "EVALUATING_ROUTES", "MAKING_DECISION", "EXPLAINING", "FORMATTING_RESPONSE"] as const;

export interface RunState {
  request_id: string;
  trip_id: string;
  conversation_id?: string | null;
  status: RunStatus;
  stage?: RunStage | null;
  intent?: Intent | null;
  missing_fields?: string[];
  recommendation_id?: string | null;
  snapshot_id?: string | null;
  decision_id?: string | null;
  degraded_services?: string[];
  error_code?: string | null;
  error_message?: string | null;
  step_count?: number;
  tool_call_count?: number;
  llm_call_count?: number;
  versions: VersionInfo;
  started_at: string;
  updated_at: string;
  completed_at?: string | null;
}

export type RunStatus = "QUEUED" | "RUNNING" | "NEEDS_INPUT" | "COMPLETED" | "PARTIAL" | "FAILED" | "CANCELLED";
export const RunStatusValues = ["QUEUED", "RUNNING", "NEEDS_INPUT", "COMPLETED", "PARTIAL", "FAILED", "CANCELLED"] as const;

export interface SafetyOverride {
  code: ReasonCode;
  minimum_risk: RiskLevel;
  event_ids?: string[];
  policy_version?: string;
}

export type Severity = "INFO" | "MINOR" | "MODERATE" | "SEVERE" | "EXTREME" | "UNKNOWN";
export const SeverityValues = ["INFO", "MINOR", "MODERATE", "SEVERE", "EXTREME", "UNKNOWN"] as const;

export interface SnapshotCreateRequest {
  travel_request: TravelRequest;
  external_context: ExternalContext;
  corridor_buffer_m?: number | null;
}

export type SourceAuthority = "OFFICIAL" | "INTERGOVERNMENTAL" | "LICENSED_PROVIDER" | "COMMUNITY" | "UNKNOWN";
export const SourceAuthorityValues = ["OFFICIAL", "INTERGOVERNMENTAL", "LICENSED_PROVIDER", "COMMUNITY", "UNKNOWN"] as const;

export interface SourceProvenance {
  source_id: string;
  provider: string;
  provider_record_id?: string | null;
  authority?: SourceAuthority;
  source_url?: string | null;
  license?: string | null;
  attribution?: string | null;
  observed_at?: string | null;
  published_at?: string | null;
  fetched_at: string;
  expires_at?: string | null;
  content_hash: string;
  schema_version?: string;
}

export interface TimeWindow {
  start: string;
  end: string;
}

export type TransportServiceStatus = "ON_TIME" | "DELAYED" | "CANCELLED" | "DISRUPTED" | "UNKNOWN";
export const TransportServiceStatusValues = ["ON_TIME", "DELAYED", "CANCELLED", "DISRUPTED", "UNKNOWN"] as const;

export interface TransportStatus {
  id: string;
  mode: TravelMode;
  operator?: string | null;
  service_number?: string | null;
  origin_stop?: string | null;
  destination_stop?: string | null;
  scheduled_departure?: string | null;
  estimated_departure?: string | null;
  scheduled_arrival?: string | null;
  estimated_arrival?: string | null;
  status?: TransportServiceStatus;
  delay_minutes?: number | null;
  cancellation?: boolean;
  message?: string | null;
  quality?: DataQuality;
  source: SourceProvenance;
}

export type TravelMode = "FLIGHT" | "TRAIN" | "BUS" | "CAR" | "WALK" | "BICYCLE" | "MULTIMODAL";
export const TravelModeValues = ["FLIGHT", "TRAIN", "BUS", "CAR", "WALK", "BICYCLE", "MULTIMODAL"] as const;

export interface TravelPreference {
  prefer_safer_route?: boolean;
  prefer_lower_cost?: boolean;
  prefer_lower_emissions?: boolean;
  max_extra_duration_minutes?: number;
  avoid_tolls?: boolean;
  accessibility?: string[];
}

export interface TravelRequest {
  request_id: string;
  trip_id: string;
  conversation_id?: string | null;
  /** Pseudonymous owner hash; never the subject id */
  user_scope_hash?: string | null;
  origin: LocationRef;
  destination: LocationRef;
  departure_time: string;
  return_time?: string | null;
  travel_modes: TravelMode[];
  preferences?: TravelPreference;
  question?: string | null;
  intent_hint?: Intent | null;
  locale?: string;
  timezone: string;
  live_location_consent_id?: string | null;
  avoid_geometries?: Polygon[];
  supersedes_request_id?: string | null;
  trip_revision?: number;
}

export interface TravelWindow {
  departure_at: string;
  arrival_at: string;
  timezone: string;
}

export interface Trip {
  id: string;
  revision: number;
  origin: LocationRef;
  destination: LocationRef;
  departure_time: string;
  return_time?: string | null;
  travel_modes: TravelMode[];
  preferences: TravelPreference;
  timezone: string;
  status?: "DRAFT" | "ACTIVE" | "COMPLETED" | "ARCHIVED" | "DELETED";
  selected_route_id?: string | null;
  previous_route_id?: string | null;
  latest_request_id?: string | null;
  latest_recommendation_id?: string | null;
  risk_acknowledged_at?: string | null;
  created_at: string;
  updated_at: string;
  deleted_at?: string | null;
}

export interface UserProfile {
  id: string;
  locale: string;
  timezone: string;
  display_name?: string | null;
  consents?: ConsentRecord[];
  has_emergency_profile?: boolean;
  created_at: string;
  updated_at: string;
}

export interface VersionInfo {
  policy?: string | null;
  prompt?: string | null;
  llm_model?: string | null;
  contract?: string;
  graph?: string | null;
  model?: string | null;
  feature_schema?: string | null;
  knowledge_collection?: string | null;
}

export interface WeatherForecastPoint {
  id: string;
  location: Point;
  valid_at: string;
  route_sample_index?: number | null;
  eta_at?: string | null;
  temperature_c?: number | null;
  apparent_temperature_c?: number | null;
  precipitation_mm?: number | null;
  precipitation_probability?: number | null;
  snowfall_cm?: number | null;
  wind_speed_kmh?: number | null;
  wind_gust_kmh?: number | null;
  visibility_m?: number | null;
  weather_code?: number | null;
  weather_category?: string | null;
  severity?: Severity;
  is_current?: boolean;
  /** DELAYED = same point sampled at ETA + delay probe */
  probe?: "PLANNED" | "DELAYED";
  probe_delay_minutes?: number | null;
  quality?: DataQuality;
  source: SourceProvenance;
}

