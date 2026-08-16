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
async function request<T>(path: string, body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: body === undefined ? "GET" : "POST",
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
  return request<MessageResponse>("/api/chat/message", {
    conversation_id: conversationId,
    message,
  });
}

export function sendDecision(
  conversationId: string,
  productId: number,
  decision: Decision
): Promise<DecisionResponse> {
  return request<DecisionResponse>("/api/chat/decision", {
    conversation_id: conversationId,
    product_id: productId,
    decision,
  });
}

export function getProfile(): Promise<Profile> {
  return request<Profile>("/api/profile");
}
