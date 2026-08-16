# Phase 4 Plan — Frontend scaffold + Chat page

Scope: `/frontend` scaffold, `api.ts`, `pages/Chat.tsx`, `components/ProductCard.tsx`. Nothing else.

**Not this phase:** `Watchlist.tsx`, `ItemDetail.tsx`, `Alerts.tsx`, `Settings.tsx`,
`DealBadge.tsx`, `ListingsTable.tsx`, `PriceHistoryChart.tsx`, react-router, MUI X Charts,
MUI DataGrid, `@mui/icons-material`. Do not create placeholder files for them.
No backend changes at all — `backend/` is untouched by this phase.

Read the "Frontend Philosophy for MVP" section of `spec.md` before writing a line. This is a
testing harness. Default MUI components as-is. No theme file, no custom colors, no spacing
system, no skeletons, no animations, no dark mode, no icons. Single column, `Container`.
If it works and is legible, it is done.

---

## 1. File list

| File | Purpose |
|---|---|
| `frontend/package.json` | deps + `dev`/`build` scripts |
| `frontend/tsconfig.json` | app TS config |
| `frontend/tsconfig.node.json` | TS config for `vite.config.ts` |
| `frontend/vite.config.ts` | react plugin + `/api` proxy to `localhost:8000` |
| `frontend/index.html` | Vite entry, `<div id="root">`, `<script type="module" src="/src/main.tsx">` |
| `frontend/src/main.tsx` | `createRoot`, `<CssBaseline />`, `<App />` |
| `frontend/src/App.tsx` | `<Container maxWidth="sm">` + `<Chat />` |
| `frontend/src/vite-env.d.ts` | `/// <reference types="vite/client" />` |
| `frontend/src/api.ts` | typed fetch wrappers + response types + `ApiError` |
| `frontend/src/pages/Chat.tsx` | message list, input, send, both branches, error surfaces |
| `frontend/src/components/ProductCard.tsx` | one product's fields + buy-now/watch buttons |

Root `.gitignore` already covers `node_modules/` and `dist/` — do not add a second one.

### Dependencies (exactly these)

```
dependencies:  react, react-dom, @mui/material, @emotion/react, @emotion/styled
devDependencies: vite, @vitejs/plugin-react, typescript, @types/react, @types/react-dom
```

No `@mui/icons-material` (spec: no icons beyond MUI defaults). No `@mui/x-charts`,
no `@mui/x-data-grid` — Phase 8 / ItemDetail adds those. No axios (`fetch` is enough).
No eslint/prettier config this phase.

### `vite.config.ts`

```
server.proxy: { "/api": { target: "http://localhost:8000", changeOrigin: true } }
```

Dev server stays on the default port 5173, per the spec's Local Testing section. Because the
proxy makes the frontend same-origin with the API, **no CORS middleware is added to
`backend/main.py`** — the backend is not modified this phase.

### `App.tsx`

No router. One page exists. `App.tsx` is:
`Container maxWidth="sm"` > `Typography variant="h5"` title ("Deal Tracker") > `<Chat />`.
Phase 8 introduces navigation when there is more than one page to navigate to.

---

## 2. `frontend/src/api.ts`

Only endpoints that exist today: `POST /api/chat/message`, `POST /api/chat/decision`,
`GET /api/profile`. No wrappers for items / listings / alerts / rescan (those routers do not
exist yet). No `updateLocation` wrapper — Phase 8's `Settings.tsx` is its only caller and adds
it then.

### 2.1 Types — must match `backend/routers/chat.py` exactly

`MessageOut` and `DecisionOut` are serialized with `response_model_exclude_unset=True`. Keys
belonging to the *other* branch are **absent**, not null. Product fields that are genuinely
null (`store_id`, `distance_miles`, `price`, ...) **are present as null**. So the branch keys
are modelled as a discriminated union on `type` / `decision`, and the null-able product fields
as `T | null`. Do not mark branch keys optional-and-nullable — that defeats the narrowing.

```ts
// mirrors ProductOut in backend/routers/chat.py. every key is always present.
export interface Product {
  product_id: number;        // index into this conversation's last results
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

// followup responses carry no narration/products keys at all
export interface FollowupResponse {
  type: "followup";
  question: string;
}

// results responses carry no question key. products may be an empty array (not an error).
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
```

### 2.2 Functions

```ts
export async function sendMessage(conversationId: string, message: string): Promise<MessageResponse>
export async function sendDecision(conversationId: string, productId: number, decision: Decision): Promise<DecisionResponse>
export async function getProfile(): Promise<Profile>
```

Bodies use snake_case keys (`conversation_id`, `product_id`) — that is the wire format;
do not add a case-converting layer.

### 2.3 `ApiError`

```ts
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string)
}
```

One shared `request()` helper does `fetch`, checks `res.ok`, and on failure builds the message
from the body:

- FastAPI `HTTPException` -> `{"detail": "some string"}` -> message is that string.
- FastAPI 422 validation -> `{"detail": [{loc, msg, type}, ...]}` -> **`detail` is an array,
  not a string.** Join the `msg` fields with `"; "`. Rendering the array directly is a React
  crash ("objects are not valid as a React child") — this is the one non-obvious bit of the
  helper and gets a one-line comment.
- Body not JSON / empty -> fall back to `HTTP ${status}`.

Network failure (backend down) throws the browser's `TypeError` from `fetch`; the helper
catches it and rethrows `new ApiError(0, "Cannot reach the backend")`.

No timeout / AbortController: a real search runs the pipeline plus two Claude calls and can
take 30+ seconds. Let it hang; the UI shows "Sending...".

---

## 3. conversation_id strategy

**Decision: one id per page load, generated with `crypto.randomUUID()` in lazy `useState`
initializer inside `Chat.tsx`. Not persisted to localStorage or sessionStorage.**

```ts
const [conversationId, setConversationId] = useState(() => crypto.randomUUID());
```

Rationale (one line for the coder to keep as a comment): the message list lives in React state
and is already lost on reload, so persisting the id would resume a server-side history the user
can no longer see — the two must reset together.

`setConversationId` exists for one purpose: the "Start new conversation" reset (below).

### Expiry handling — backend restart

The backend keeps conversations in a module-level dict, evicts past 50, and loses everything on
restart (including every `uvicorn --reload` save). `/api/chat/message` never 404s
(`get_conversation` creates on miss) — but a message sent to a *revived* id starts from an empty
server-side history, so the extractor asks its first followup again. That is acceptable and needs
no special handling.

`/api/chat/decision` **does** 404 with `"conversation not found or expired"`, and this will
happen constantly in development.

On **any 404 from `/api/chat/decision`**:
1. Append an `error` entry showing the backend `detail`.
2. Set `conversationExpired = true`.
3. Render a `Button` "Start new conversation" above the input, which does
   `setConversationId(crypto.randomUUID()); setEntries([]); setConversationExpired(false);`.
4. While `conversationExpired` is true, all product buttons are disabled (their `product_id`
   indexes a result set the server no longer has).

All three 404 details (`conversation not found or expired`, `no results in this conversation yet`,
`unknown product_id`) get the same treatment — they all mean the same thing from the client's
side: the server no longer holds the results these cards came from.

---

## 4. `frontend/src/pages/Chat.tsx`

### 4.1 State

```ts
type ChatEntry =
  | { kind: "user"; text: string }
  | { kind: "followup"; text: string }                                 // question
  | { kind: "results"; narration: string; products: Product[] }
  | { kind: "system"; text: string }                                   // decision confirmation
  | { kind: "error"; text: string };

const [entries, setEntries] = useState<ChatEntry[]>([]);
const [input, setInput] = useState("");
const [sending, setSending] = useState(false);              // message in flight
const [busyProductId, setBusyProductId] = useState<number | null>(null);  // decision in flight
const [conversationId, setConversationId] = useState(() => crypto.randomUUID());
const [conversationExpired, setConversationExpired] = useState(false);
const [locationMissing, setLocationMissing] = useState(false);
```

That is the whole state. No reducer, no context, no custom hooks.

### 4.2 Mount effect

`useEffect(() => { getProfile() ... }, [])`: if `lat === null || lon === null`, set
`locationMissing = true`. Failure of this call is swallowed (the 400 path below catches the same
condition anyway) — one comment saying so.

### 4.3 No-location handling (Settings.tsx does not exist until Phase 8)

The user has no in-app way to set a location this phase, and building one here would be building
`Settings.tsx` early. **Decision: Chat surfaces it as instructions, not as a form.**

When `locationMissing` is true, render a persistent MUI `Alert severity="warning"` at the top of
the page containing plain text:

```
No location set. Chat searches will fail until you set one. From a terminal:
curl.exe -X PATCH http://localhost:8000/api/profile/location -H "Content-Type: application/json" -d "{\"lat\":37.7749,\"lon\":-122.4194,\"display_address\":\"San Francisco, CA\"}"
```

The command goes in a `<Typography component="pre">` so it is selectable/copyable. No styling
beyond that. Phase 8's `Settings.tsx` replaces this alert with a real input; leave a one-line
comment saying so.

A 400 response from `/api/chat/message` also sets `locationMissing = true`, so the banner appears
even if the profile call at mount succeeded earlier (e.g. the DB was edited mid-session).

### 4.4 Send flow

`handleSend()`:
1. `const text = input.trim()`; return early if empty or `sending` (Send button is also
   `disabled` in both cases — this prevents the only realistic 422).
2. Append `{ kind: "user", text }`, clear `input`, `setSending(true)`.
3. `await sendMessage(conversationId, text)`.
4. `response.type === "followup"` -> append `{ kind: "followup", text: response.question }`.
5. `response.type === "results"` -> append
   `{ kind: "results", narration: response.narration, products: response.products }`.
   `products: []` is a normal result, not an error — render the narration and the plain text
   "No products." underneath.
6. `catch (error)` -> section 6 table.
7. `finally { setSending(false) }`.

Branch on `response.type` only. Never read `response.products` before narrowing — on a followup
that key does not exist.

### 4.5 Decision flow

`handleDecision(productId, decision)`:
1. `setBusyProductId(productId)`.
2. `await sendDecision(conversationId, productId, decision)`.
3. Append `{ kind: "system", text: response.message }`. For `buy_now`, if `response.url` is
   non-null, also render the url as a plain `<Link href={url} target="_blank">` inside that
   entry — that is the whole point of buy_now. For `watch`, append the `item_id` to the text
   (`Watching X. (item 3)`) so the tester can find the row in SQLite.
4. `catch` -> section 6 table.
5. `finally { setBusyProductId(null) }`.

Narrowing on `response.decision` is needed to read `item_id` — a buy_now response has no
`item_id` key.

### 4.6 Render (top to bottom, single column)

1. Location `Alert` (conditional, 4.3).
2. Conversation-expired `Alert` + "Start new conversation" `Button` (conditional, section 3).
3. `Stack spacing={2}` of entries, in order:
   - `user`: `Typography` prefixed `You: `.
   - `followup`: `Typography` prefixed `Assistant: `.
   - `results`: `Typography` with the narration, then the `ProductCard` list (or "No products.").
   - `system`: `Typography` (plus link for buy_now).
   - `error`: `Alert severity="error"` with the message.
4. `"Sending..."` `Typography` while `sending` (spec: a spinner or "Loading..." text is enough;
   plain text is fine, no `CircularProgress` needed).
5. Input row: `TextField fullWidth` (multiline off, `onKeyDown` Enter submits) +
   `Button variant="contained"` "Send", both `disabled={sending}`.

No auto-scroll, no message bubbles, no avatars, no timestamps.

---

## 5. `frontend/src/components/ProductCard.tsx`

Presentational plus two buttons. It does **not** call the API — `Chat.tsx` owns the fetch so the
confirmation can be appended to the message list.

```ts
interface ProductCardProps {
  product: Product;
  onDecision: (productId: number, decision: Decision) => void;
  busy: boolean;      // a decision for this card is in flight
  disabled: boolean;  // conversation expired
}
```

MUI `Card` > `CardContent` + `CardActions`. Fields rendered, each a plain `Typography` line:

- name (as `Link href={product.url}` when `url` is non-null, otherwise plain text; `name` can be
  null -> render `(unnamed)`)
- price: `$24.99` via `toFixed(2)`, or `price unavailable` when null
- retailer
- `in stock` / `out of stock` / `stock unknown` (it is `boolean | null`)
- rating + review count when `rating` is non-null, e.g. `4.7 (1843 reviews)`
- `store_id` line only when non-null; `distance_miles` line only when non-null (Best Buy search
  returns online rows, so these are null in practice — render nothing rather than "null")
- score line: `score 0.71 | spec 0.50 review 0.94 price 1.00 distance 0.50 nice 0.50`
  (all `toFixed(2)`). This is a testing harness — seeing the ranking sub-scores is the point.

`CardActions`: two `Button`s, "Buy now" and "Watch", both `disabled={busy || disabled}`, each
calling `onDecision(product.product_id, ...)`.

No `sx` beyond what layout requires. No conditional coloring, no badges, no images (the product
payload has no image field anyway).

---

## 6. Error handling — every status code

Handled in `Chat.tsx`'s catch blocks by inspecting `error instanceof ApiError` and
`error.status`. All messages shown to the user come from the backend `detail` where one exists.

| Status | Where | Backend detail | UI behaviour |
|---|---|---|---|
| 400 | `/chat/message` | `Set your location first: PATCH /api/profile/location` | `error` entry with the detail, **and** `setLocationMissing(true)` so the banner (4.3) with the copyable curl command appears. The user's message was dropped server-side (the router pops it from history), so also restore it into the input box for a retry after setting the location. |
| 400 | `/chat/decision` | `product has no url` | `error` entry with the detail. Nothing else; the other cards still work. |
| 404 | `/chat/decision` | 3 possible details | `error` entry + `conversationExpired = true` + "Start new conversation" button, product buttons disabled (section 3). |
| 404 | `/chat/message` | — | Cannot happen (`get_conversation` creates on miss). Falls through to the generic branch; do not write a special case. |
| 422 | either | `detail` is an **array** | `error` entry with the joined `msg` strings from `api.ts` (section 2.3). Should not occur in normal use — the Send button is disabled on empty input. |
| 502 | `/chat/message` | `criteria extraction failed` | `error` entry: the detail plus `Try again.` The backend appended nothing to history before raising, so restore the user's text into the input box for a clean retry. |
| 500 | either | FastAPI default HTML/JSON | Generic `error` entry `Request failed (500)`. No retry logic. |
| 0 | either | — | `Cannot reach the backend` (uvicorn not running). |

Rule: never swallow an error silently, never `alert()`, never `console.error` as the only
surface. Every failure produces a visible `error` entry.

---

## 7. Coder verification checklist

Backend must be running: `uvicorn backend.main:app --reload --port 8000`, with a real
`ANTHROPIC_API_KEY` in `.env` and `BESTBUY_API_KEY` blank (product data comes from Best Buy
fixtures). Frontend: `cd frontend && npm install && npm run dev`.

### Setup

1. `npm install` completes with no errors; `npm run dev` serves on `http://localhost:5173`.
2. `npm run build` (`tsc && vite build`) passes with **zero** TypeScript errors.
3. Set the location first — every search 400s until this runs:
   `curl.exe -X PATCH http://localhost:8000/api/profile/location -H "Content-Type: application/json" -d "{\"lat\":37.7749,\"lon\":-122.4194,\"display_address\":\"San Francisco, CA\"}"`
   -> 200.

### Browser pass (Playwright MCP tools, real browser)

4. Navigate to `http://localhost:5173`. Page renders, no console errors, no location warning
   banner (step 3 set it).
5. Snapshot the page: exactly one text input and one "Send" button. Send is disabled while the
   input is empty.
6. Type `i need a portable charger under $150` into the input, click Send.
7. Assert `Sending...` appears while the request is in flight.
8. Assert an assistant followup question renders as text (the live extractor decides the wording,
   so assert on *presence of a new assistant entry ending in "?"*, not on exact copy). Assert
   **no** product cards are present at this point.
9. Type an answer (`shipped is fine, at least 20000 mAh, budget $150`), click Send.
10. Wait for the results entry (this runs the pipeline + narration, allow up to 60s). Assert a
    narration paragraph renders **and** at least one product card renders.
11. Assert one card shows: a product name linking to a `bestbuy.com` url, a `$` price, the
    retailer `bestbuy`, and the `score` line. Assert no literal `null` or `undefined` text
    appears anywhere on the page.
12. Click "Watch" on the first card. Assert a confirmation entry appears containing `Watching`
    and an item id, and that the buttons were disabled while it was in flight.
13. Verify the DB write:
    `python -c "import sqlite3;c=sqlite3.connect('app.db');print([c.execute(f'select count(*) from {t}').fetchone()[0] for t in ('items','listings','price_history')])"`
    -> `[1, 1, 1]`.
14. Click "Buy now" on another card. Assert a confirmation entry with a clickable bestbuy.com
    link, and that the counts from step 13 are **unchanged** (buy_now writes nothing).

### Error paths

15. Expiry: restart uvicorn (or save a backend file to trigger `--reload`), then click "Watch" on
    an existing card. Assert an error entry with `conversation not found or expired`, a
    "Start new conversation" button, and that all product buttons are now disabled. Click the
    button -> message list clears and the input is usable again.
16. No location: `python -c "import sqlite3;c=sqlite3.connect('app.db');c.execute('update profile set lat=null,lon=null');c.commit()"`,
    reload the page. Assert the warning banner with the `curl.exe` command renders. Drive a
    conversation to the search turn and assert the 400 detail appears as an error entry and the
    typed message is restored to the input box. Restore the location (step 3) afterwards.
17. Backend down: stop uvicorn, send a message. Assert `Cannot reach the backend` renders as an
    error entry, not a blank screen or an unhandled promise rejection in the console.

### Mobile viewport (spec: Responsive Design)

18. Resize the browser to 390x844 and repeat steps 6-12. Assert: single column, **no horizontal
    page scroll**, the input and Send button both visible and clickable, product card text wraps
    rather than overflowing, and the long `curl.exe` line in the warning banner does not force
    the page wider than the viewport (wrap it).

### Hygiene

19. `git status`: new files match section 1 exactly. No `Watchlist.tsx`, `ItemDetail.tsx`,
    `Alerts.tsx`, `Settings.tsx`, `DealBadge.tsx`, `ListingsTable.tsx`, `PriceHistoryChart.tsx`,
    no `theme.ts`, no `.css` file, no `node_modules/` or `dist/` tracked.
20. `grep -rn "createTheme\|ThemeProvider\|palette" frontend/src` -> zero matches (no custom
    theming).
21. `grep -rn "any" frontend/src` -> zero matches outside the JSON-body parse in `api.ts`.
22. `grep -rn "localhost:8000" frontend/src` -> zero matches. All calls are relative `/api/...`
    through the Vite proxy (the copyable curl command in Chat.tsx is the one allowed literal —
    it is user-facing text, not a request).
23. No emojis anywhere in the diff.

---

## 8. Decisions not covered by spec.md (flagging for the user)

1. **conversation_id lifetime** — spec never says where it comes from. Chose `crypto.randomUUID()`
   per page load, not persisted, so the id and the visible message list die together.
2. **No-location UX** — spec assumes `Settings.tsx` exists; it does not until Phase 8. Chose a
   warning banner with a copyable `curl.exe` command rather than building a location input early.
   Say the word if you would rather pull a minimal lat/lon input into Chat now.
3. **No router** — one page, so `App.tsx` renders `Chat` directly. Phase 8 adds routing.
4. **Sub-scores on the card** — spec's ProductCard says "display fields" without listing them.
   Included the five ranking sub-scores because this is a harness for validating ranking.
5. **`updateLocation` omitted from `api.ts`** — the endpoint exists but has no caller until
   Phase 8's Settings page.
6. **502/400 restore the typed message into the input** — spec is silent; chosen because the
   backend discards the turn in both cases, so a retry is clean.
