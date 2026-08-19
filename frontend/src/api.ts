// mirrors ProductOut in backend/routers/chat.py. every key is always present.
export interface Product {
  product_id: number; // index into this conversation's last results
  name: string | null;
  url: string | null;
  price: number | null;
  in_stock: boolean | null;
  retailer: string;
  store_id: string | null;
  distance_miles: number | null;
  rating: number | null;
  review_count: number | null;
  rating_source: string | null; // "amazon" is first-party, "*_inherited" came from another listing
  final_score: number;
  spec_match: number;
  review_score: number;
  price_score: number;
  distance_score: number;
  nice_to_have_score: number;
  specs_inherited_from: string | null; // retailer these specs were attributed from, if any
  video_url: string | null; // a review video, only for products the research stage reached
  // other listings of the same product folded into this one - other colours, or the same
  // model at another retailer. usually empty
  variants: { name: string | null; url: string | null; price: number | null; retailer: string }[];
  // what each source said. only the researched top few carry reddit/youtube rows
  sources: {
    source: string | null;
    url: string | null;
    rating: number | null;
    review_count: number | null;
    mention_count: number | null;
    summary: string | null;
  }[];
  sentiment: string | null;
}

// the backend serializes with exclude_unset, so followups carry no narration/products keys
export interface FollowupResponse {
  type: "followup";
  question: string;
}

// results carry no question key. products may be an empty array, which is not an error.
export interface ResultsResponse {
  type: "results";
  narration: string;
  products: Product[];
  debug?: DebugTrace; // per-stage trace, present only while the backend emits one
}

export type MessageResponse = FollowupResponse | ResultsResponse;

// the debug trace is owned by another agent and still settling, so it is read structurally
// rather than typed field by field: unknown values, narrowed at the point of display
export type DebugTrace = Record<string, unknown>;

// live state of an in-flight search, polled by the waiting screen. running:false means the
// run has ended - the /chat/message response is what carries the results, not this
export interface SearchProgress {
  running: boolean;
  stage?: string | null;
  elapsed_ms?: number;
  retailers?: { retailer: string; outcome: string; candidates_kept: number | null }[];
  products_in?: number | null;
  qualified?: number | null;
  researched?: number;
}

// buy_now carries no item_id key
export interface BuyNowResponse {
  decision: "buy_now";
  url: string | null;
  message: string;
}

// watch always has item_id; url is non-null because the backend 400s on a product without one
export interface WatchResponse {
  decision: "watch";
  url: string;
  item_id: number;
  message: string;
}

export type DecisionResponse = BuyNowResponse | WatchResponse;

export type Decision = "buy_now" | "watch";

// mirrors ConversationSummary. conversations are persisted now, so past ones can be reopened
export interface ConversationSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ConversationTurn {
  role: string; // user | assistant
  content: string;
}

export interface ConversationDetail {
  id: string;
  history: ConversationTurn[];
  created_at: string;
  updated_at: string;
}

export interface Profile {
  id: number;
  lat: number | null;
  lon: number | null;
  display_address: string | null;
}

// mirrors ItemOut in backend/routers/items.py
export interface Item {
  id: number;
  name: string | null;
  category: string | null;
  criteria_json: string | null;
  budget_max: number | null;
  target_price: number | null;
  fulfillment_preference: string | null;
  radius_miles: number | null;
  min_review_count: number | null;
  status: string | null; // watching | archived
}

// manual add, skips chat. only name is required; the rest default server-side
export interface NewItem {
  name: string;
  category?: string;
  budget_max?: number;
  target_price?: number;
}

// mirrors ListingOut
export interface Listing {
  id: number;
  item_id: number;
  retailer: string | null;
  store_id: string | null; // null = online
  store_name: string | null;
  distance_miles: number | null;
  url: string | null;
  price: number | null;
  in_stock: boolean | null;
  shipping_days_est: number | null;
  scraped_at: string | null;
}

// mirrors PricePointOut
export interface PricePoint {
  id: number;
  listing_id: number;
  price: number | null;
  recorded_at: string | null;
}

// mirrors ReviewOut. sources ending in _inherited were attributed from another retailer
export interface Review {
  id: number;
  source: string | null;
  rating: number | null;
  review_count: number | null;
  verified_ratio: number | null;
  rating_distribution_json: string | null;
  authenticity_flag: string | null;
  url: string | null;
  summary_text: string | null;
  fetched_at: string | null;
}

// mirrors AlertOut, which joins the item name and the listing's retailer/url/price
export interface Alert {
  id: number;
  item_id: number | null;
  item_name: string | null;
  listing_id: number | null;
  retailer: string | null;
  url: string | null;
  price: number | null;
  reason: string | null; // price_drop | target_hit | new_alternative
  sent_at: string | null;
}

export interface RescanResult {
  item_id: number;
  listings_seen: number;
  alerts: string[];
  emails_sent: number;
}

// product urls come from retailer APIs/scrapes, so only http(s) is safe in an href
export function safeUrl(url: string | null): string | null {
  return url !== null && /^https?:\/\//i.test(url) ? url : null;
}

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// FastAPI sends {"detail": "..."} for HTTPException but {"detail": [{msg, loc, type}, ...]}
// for 422 validation errors - the array would crash React if rendered, so join the msg fields
function errorMessage(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((entry) => (entry as { msg?: string }).msg ?? "invalid input").join("; ");
  }
  return `HTTP ${status}`;
}

// no timeout: a real search runs the pipeline plus two Claude calls and can take 30+ seconds
async function request<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    // fetch only rejects on network failure, so uvicorn is not running
    throw new ApiError(0, "Cannot reach the backend");
  }

  if (!response.ok) {
    // error bodies are not always JSON (500s can be HTML)
    const parsed = await response.json().catch(() => null);
    throw new ApiError(response.status, errorMessage(parsed, response.status));
  }
  return (await response.json()) as T;
}

export function sendMessage(conversationId: string, message: string): Promise<MessageResponse> {
  return request<MessageResponse>("/api/chat/message", "POST", {
    conversation_id: conversationId,
    message,
  });
}

export function sendDecision(
  conversationId: string,
  productId: number,
  decision: Decision
): Promise<DecisionResponse> {
  return request<DecisionResponse>("/api/chat/decision", "POST", {
    conversation_id: conversationId,
    product_id: productId,
    decision,
  });
}

export function getConversations(): Promise<ConversationSummary[]> {
  return request<ConversationSummary[]>("/api/conversations");
}

export function getConversation(conversationId: string): Promise<ConversationDetail> {
  return request<ConversationDetail>(`/api/conversations/${conversationId}`);
}

// the trace of the most recent search, for the debug panel. 404s until a search has run
export function getLastDebug(): Promise<DebugTrace> {
  return request<DebugTrace>("/api/debug/last");
}

export function getSearchProgress(conversationId: string): Promise<SearchProgress> {
  return request<SearchProgress>(`/api/chat/progress/${conversationId}`);
}

export function getProfile(): Promise<Profile> {
  return request<Profile>("/api/profile");
}

export function updateLocation(
  lat: number,
  lon: number,
  displayAddress: string
): Promise<Profile> {
  return request<Profile>("/api/profile/location", "PATCH", {
    lat,
    lon,
    display_address: displayAddress,
  });
}

export function getItems(): Promise<Item[]> {
  return request<Item[]>("/api/items");
}

export function getItem(itemId: number): Promise<Item> {
  return request<Item>(`/api/items/${itemId}`);
}

export function createItem(item: NewItem): Promise<Item> {
  return request<Item>("/api/items", "POST", item);
}

export function deleteItem(itemId: number): Promise<{ deleted: number }> {
  return request<{ deleted: number }>(`/api/items/${itemId}`, "DELETE");
}

// runs a real scrape for one item, so it can take a while
export function rescanItem(itemId: number): Promise<RescanResult> {
  return request<RescanResult>(`/api/items/${itemId}/rescan`, "POST", {});
}

export function getListings(itemId: number): Promise<Listing[]> {
  return request<Listing[]>(`/api/items/${itemId}/listings`);
}

export function getPriceHistory(itemId: number): Promise<PricePoint[]> {
  return request<PricePoint[]>(`/api/items/${itemId}/price-history`);
}

export function getReviews(itemId: number): Promise<Review[]> {
  return request<Review[]>(`/api/items/${itemId}/reviews`);
}

export function getAlerts(): Promise<Alert[]> {
  return request<Alert[]>("/api/alerts");
}
