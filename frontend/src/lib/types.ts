/** 公开接口类型与 docs/API_CONTRACT.md 一致，未知值保持 null。 */
export interface AuthUser { id: string; email: string }
export interface AuthResponse {
  access_token: string;
  token_type: 'bearer';
  user: AuthUser;
}
export interface SessionSummary {
  session_id: string;
  title: string;
  trip_id: string | null;
  current_version_no: number | null;
  created_at: string;
  updated_at: string;
}

export interface TripRequest {
  destination: string | null;
  origin: string | null;
  start_date: string | null;
  days: number | null;
  budget: number | null;
  traveler_count: number;
  preferences: string[];
  constraints: string[];
  pace: 'relaxed' | 'balanced' | 'compact';
}

/** 景点坐标采用 GCJ-02；演示来源不得标记为真实高德查询。 */
export interface SearchCandidate {
  place_id: string;
  name: string;
  category: string;
  address: string | null;
  city: string;
  longitude: number;
  latitude: number;
  source: 'amap' | 'demo';
  estimated_cost: null;
  opening_hours: null;
  indoor: null;
  image_url?: string | null;
  image_credit?: string | null;
}
export type Candidate = SearchCandidate;
export interface Recommendation { place_id: string; reason: string }
export interface SearchResult {
  candidates: SearchCandidate[];
  recommendations: Recommendation[];
  data_sources: string[];
  unmet_conditions: string[];
  summary: string;
  limit_reached: boolean;
}
export type RunStatus = 'queued' | 'running' | 'completed' | 'needs_input' | 'failed';
export type RunIntent = 'create' | 'revise' | 'direct_search' | 'knowledge' | 'other' | null;
export interface PublicError {
  code: string;
  message: string;
  retryable: boolean;
  details: Record<string, unknown>;
}
export interface ItineraryItem {
  item_id: string; place_id: string; name: string; start_time: string;
  duration_minutes: number; travel_from_previous_minutes: number;
  address: string | null; notes: string; estimated_cost: number | null;
}
export interface Itinerary {
  summary: string;
  days: Array<{
    day_index: number; date: string | null; items: ItineraryItem[];
    total_cost: number | null; walking_distance_km: number; warnings: string[];
  }>;
  total_cost: number | null;
  currency: 'CNY';
}
export interface Route {
  from_item_id: string; to_item_id: string; distance_km: number;
  duration_minutes: number; mode: 'walking' | 'driving'; provider: string;
}
export interface Validation {
  passed: boolean;
  issues: Array<{ severity: 'error' | 'warning'; code: string; message: string; day_index: number | null }>;
  checked_at: string;
}
export interface RunResult {
  trip_request: TripRequest | null;
  search: SearchResult | null;
  itinerary: Itinerary | null;
  routes: Route[] | null;
  validation: Validation | null;
  saved_version: { trip_id: string; version_no: number } | null;
}
export interface VersionSummary {
  trip_id: string;
  version_no: number;
  source_run_id: string;
  summary: string;
  created_at: string;
}
export interface ItineraryVersion extends Omit<VersionSummary, 'summary'> {
  trip_request: TripRequest;
  itinerary: Itinerary;
  routes: Route[];
  validation: Validation;
}
/** 一条消息对应一次运行；needs_input 同样属于不可继续修改的终态。 */
export interface RunView {
  run_id: string;
  session_id: string;
  client_request_id: string;
  message: string;
  status: RunStatus;
  intent: RunIntent;
  base_version_no: number | null;
  response: string | null;
  pending_question: string | null;
  missing_fields: string[];
  result: RunResult;
  error: PublicError | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}
export interface SessionView {
  session_id: string;
  trip_id: string | null;
  current_version_no: number | null;
  trip_request: TripRequest | null;
  latest_run_id: string | null;
  active_run_id: string | null;
  pending_question: string | null;
  created_at: string;
  updated_at: string;
}
export interface RunReceipt {
  run_id: string; session_id: string; status: RunStatus;
  status_url: string; events_url: string;
}
export interface Page<T> { items: T[]; next_cursor: string | null }
export interface RunSubmission {
  client_request_id: string;
  message: string;
  expected_version_no: number | null;
}
export interface Progress {
  stage: 'queued' | 'understand' | 'search' | 'plan' | 'validate' | 'save';
  message: string;
}
export interface RunEvent<T = unknown> {
  run_id: string; seq: number; occurred_at: string; data: T;
}

/** SSE 与轮询共用终态判断，避免追问轮次被误认为仍在执行。 */
export function isTerminal(status: RunStatus): boolean {
  return status === 'completed' || status === 'needs_input' || status === 'failed';
}

export function emptyResult(): RunResult {
  return { trip_request: null, search: null, itinerary: null, routes: null, validation: null, saved_version: null };
}
