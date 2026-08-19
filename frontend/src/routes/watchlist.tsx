import { Link, createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import type { Alert, Item, Listing } from "@/api";
import { ApiError, createItem, deleteItem, getAlerts, getItems, getListings, rescanItem } from "@/api";
import { AppShell } from "@/components/shopper/AppShell";
import { dealLabel, money, retailerLabel, timeAgo } from "@/lib/format";

export const Route = createFileRoute("/watchlist")({ component: WatchlistPage });

// GET /api/items carries no prices, so the "best option now" line comes from each item's
// listings (the endpoint returns them cheapest first) and the badge from its newest alert
async function loadBest(items: Item[]): Promise<Record<number, Listing | undefined>> {
  const lists = await Promise.all(items.map((item) => getListings(item.id).catch(() => [])));
  return Object.fromEntries(items.map((item, index) => [item.id, lists[index]?.[0]]));
}

function WatchlistPage() {
  const [items, setItems] = useState<Item[]>([]);
  const [best, setBest] = useState<Record<number, Listing | undefined>>({});
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [rescanning, setRescanning] = useState<number | null>(null);
  const [draft, setDraft] = useState("");

  useEffect(() => {
    void load();
  }, []);

  async function load() {
    setLoading(true);
    try {
      const [nextItems, nextAlerts] = await Promise.all([getItems(), getAlerts().catch(() => [])]);
      setItems(nextItems);
      setAlerts(nextAlerts);
      setBest(await loadBest(nextItems));
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Request failed");
    } finally {
      setLoading(false);
    }
  }

  async function add() {
    const name = draft.trim();
    if (!name) return;
    try {
      await createItem({ name });
      setDraft("");
      setNotice(`Watching ${name}.`);
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Request failed");
    }
  }

  async function remove(item: Item) {
    try {
      await deleteItem(item.id);
      setNotice(`Removed ${item.name ?? "item"}.`);
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Request failed");
    }
  }

  // a rescan runs the whole pipeline synchronously, so it is as slow as a chat search
  async function rescan(item: Item) {
    setRescanning(item.id);
    setNotice(null);
    try {
      const result = await rescanItem(item.id);
      setNotice(
        `Rescanned ${item.name ?? "item"}: ${result.listings_seen} listings, ${result.alerts.length} alerts.`
      );
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Request failed");
    } finally {
      setRescanning(null);
    }
  }

  function latestReason(itemId: number): string | null {
    return alerts.find((alert) => alert.item_id === itemId)?.reason ?? null;
  }

  return (
    <AppShell title="Watchlist" subtitle="Rechecked every 6 hours.">
      <div className="sticker mb-6 flex flex-wrap gap-2 rounded-3xl bg-card p-4">
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") void add();
          }}
          placeholder="add an item by name..."
          className="sticker w-full min-w-0 flex-1 rounded-full bg-background px-4 py-2 text-sm font-semibold outline-none placeholder:text-muted-foreground"
        />
        <button
          onClick={() => void add()}
          className="sticker rounded-full bg-primary px-4 py-2 text-sm font-extrabold text-primary-foreground"
        >
          Add
        </button>
      </div>

      {error ? (
        <p className="sticker mb-4 rounded-3xl bg-strawberry px-4 py-2 text-sm font-semibold text-accent-foreground">
          {error}
        </p>
      ) : null}
      {notice ? (
        <p className="sticker mb-4 rounded-3xl bg-sky px-4 py-2 text-sm font-semibold">{notice}</p>
      ) : null}

      <div className="grid gap-4 md:grid-cols-2">
        {items.map((item) => {
          const listing = best[item.id];
          const reason = latestReason(item.id);
          return (
            <article key={item.id} className="sticker rounded-3xl bg-card p-4">
              <div className="flex items-start gap-2">
                <div className="min-w-0">
                  <h2 className="font-display text-xl font-extrabold leading-tight break-words">
                    {item.name ?? "Unnamed item"}
                  </h2>
                  <p className="text-sm text-muted-foreground">
                    {item.category ?? "uncategorised"} · budget {money(item.budget_max)} · target{" "}
                    {money(item.target_price)}
                  </p>
                </div>
                {reason ? (
                  <span className="sticker ml-auto shrink-0 rounded-full bg-strawberry px-2.5 py-1 text-xs font-extrabold text-accent-foreground">
                    {dealLabel[reason] ?? reason}
                  </span>
                ) : null}
              </div>

              <div className="mt-3 rounded-2xl bg-secondary/60 p-3">
                <p className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                  Best option now
                </p>
                {listing ? (
                  <>
                    <p className="font-display text-lg font-bold leading-tight">
                      {retailerLabel(listing.retailer)}
                    </p>
                    <p className="text-sm font-semibold">
                      {money(listing.price)} ·{" "}
                      {listing.in_stock === null
                        ? "stock unknown"
                        : listing.in_stock
                          ? "in stock"
                          : "out of stock"}
                      {listing.shipping_days_est !== null
                        ? ` · ships in ${listing.shipping_days_est}d`
                        : ""}
                    </p>
                  </>
                ) : (
                  <p className="text-sm font-semibold">No listings found yet.</p>
                )}
              </div>

              <div className="mt-4 flex flex-wrap items-center gap-2">
                <Link
                  to="/items/$id"
                  params={{ id: String(item.id) }}
                  className="sticker rounded-full bg-primary px-3 py-2 text-sm font-extrabold text-primary-foreground transition-transform hover:-translate-y-0.5"
                >
                  View detail
                </Link>
                <button
                  onClick={() => void rescan(item)}
                  disabled={rescanning !== null}
                  className="sticker rounded-full bg-card px-3 py-2 text-sm font-extrabold transition-transform hover:-translate-y-0.5 disabled:opacity-50"
                >
                  {rescanning === item.id ? "Rescanning (30-60s)..." : "Rescan"}
                </button>
                <button
                  onClick={() => void remove(item)}
                  className="sticker rounded-full bg-card px-3 py-2 text-sm font-extrabold transition-transform hover:-translate-y-0.5"
                >
                  Delete
                </button>
                <span className="ml-auto text-xs font-semibold text-muted-foreground">
                  checked {timeAgo(listing?.scraped_at ?? null)}
                </span>
              </div>
            </article>
          );
        })}
      </div>

      {!loading && items.length === 0 ? (
        <p className="sticker rounded-3xl bg-card p-4 text-sm font-semibold text-muted-foreground">
          Nothing watched yet. Add one above, or pick Watch on a chat result.
        </p>
      ) : null}
    </AppShell>
  );
}
