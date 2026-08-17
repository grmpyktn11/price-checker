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
  final_score: number;
  spec_match: number;
  review_score: number;
  price_score: number;
  distance_score: number;
  nice_to_have_score: number;
  specs_inherited_from: string | null; // retailer these specs were attributed from, if any
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
}

export type MessageResponse = FollowupResponse | ResultsResponse;

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

export interface Profile {
  id: number;
  lat: number | null;
  lon: number | null;
  display_address: string | null;
}

export type Decision = "buy_now" | "watch";

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

export function deleteItem(itemId: number): Promise<unknown> {
  return request<unknown>(`/api/items/${itemId}`, "DELETE");
}

// runs a real scrape for one item, so it can take a while
export function rescanItem(itemId: number): Promise<unknown> {
  return request<unknown>(`/api/items/${itemId}/rescan`, "POST", {});
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

export interface Status {
  live_scrape: boolean;
}

export function getStatus(): Promise<Status> {
  return request<Status>("/api/status");
}
