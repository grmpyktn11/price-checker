import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { Item, Listing, PricePoint, Review } from "@/api";
import { ApiError, getItem, getListings, getPriceHistory, getReviews, safeUrl } from "@/api";
import { AppShell } from "@/components/shopper/AppShell";
import {
  isInherited,
  money,
  parseUtc,
  retailerLabel,
  shortDateTime,
} from "@/lib/format";

export const Route = createFileRoute("/items/$id")({ component: ItemDetailPage });

// picked for contrast against each other, not for order: chart-3 is the pale butter tone and
// would disappear on the card
const seriesColors = ["var(--chart-1)", "var(--chart-2)", "var(--sky)", "var(--foreground)"];

// one row per timestamp, one column per listing. sparse by construction: a point is only
// written when that listing's price changed, so most cells are undefined and the lines are
// drawn with connectNulls across the gaps
type ChartRow = { t: number } & Record<string, number | undefined>;

interface Chart {
  rows: ChartRow[];
  series: { key: string; label: string }[];
}

function toChart(history: PricePoint[], listings: Listing[]): Chart {
  const rows = new Map<number, ChartRow>();
  const series = new Map<string, string>();
  for (const point of history) {
    const at = parseUtc(point.recorded_at);
    if (at === null || point.price === null) continue;
    const key = `listing_${point.listing_id}`;
    const retailer = listings.find((l) => l.id === point.listing_id)?.retailer ?? null;
    series.set(key, `${retailerLabel(retailer)} #${point.listing_id}`);
    const row = rows.get(at.getTime()) ?? { t: at.getTime() };
    row[key] = point.price;
    rows.set(at.getTime(), row);
  }
  return {
    // recharts places points by row order, so the rows have to be in time order
    rows: [...rows.values()].sort((a, b) => a.t - b.t),
    series: [...series.entries()].map(([key, label]) => ({ key, label })),
  };
}

function PriceChart({ chart }: { chart: Chart }) {
  if (chart.series.length === 0) {
    return (
      <p className="py-8 text-sm font-semibold text-muted-foreground">
        No price history recorded yet.
      </p>
    );
  }
  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={chart.rows} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
        <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
        <XAxis
          dataKey="t"
          type="number"
          domain={["dataMin", "dataMax"]}
          tickFormatter={(value: number) => new Date(value).toLocaleDateString()}
          tick={{ fontSize: 11 }}
        />
        <YAxis tick={{ fontSize: 11 }} domain={["auto", "auto"]} />
        <Tooltip
          labelFormatter={(value: number) => new Date(value).toLocaleString()}
          formatter={(value: number) => `$${value.toFixed(2)}`}
        />
        <Legend wrapperStyle={{ fontSize: 11, fontWeight: 700 }} />
        {chart.series.map((line, index) => (
          <Line
            key={line.key}
            type="monotone"
            dataKey={line.key}
            name={line.label}
            connectNulls
            stroke={seriesColors[index % seriesColors.length]}
            strokeWidth={3}
            dot={{ r: 3 }}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

// a reddit summary is several whole threads stitched together - thousands of characters. shown
// in full it buries the rest of the page, and on a phone it is several screens of scrolling
// before you reach the listings
const SUMMARY_PREVIEW_CHARS = 280;


function ReviewRow({ review }: { review: Review }) {
  const [expanded, setExpanded] = useState(false);
  const href = safeUrl(review.url);
  // reddit and youtube rows are discussion, not ratings: they carry no stars to show
  const isDiscussion = review.rating === null;
  const summary = review.summary_text ?? "";
  const isLong = summary.length > SUMMARY_PREVIEW_CHARS;
  const shown = expanded || !isLong ? summary : `${summary.slice(0, SUMMARY_PREVIEW_CHARS)}…`;
  return (
    <li className="rounded-2xl bg-secondary/60 p-3">
      <p className="font-bold">
        {retailerLabel(review.source)}
        {isInherited(review.source) ? (
          <span className="ml-2 inline-block whitespace-nowrap rounded-full bg-butter px-2 py-0.5 text-xs font-extrabold">
            borrowed from another listing
          </span>
        ) : null}
        {review.authenticity_flag && review.authenticity_flag !== "ok" ? (
          <span className="ml-2 inline-block whitespace-nowrap rounded-full bg-strawberry px-2 py-0.5 text-xs font-extrabold text-accent-foreground">
            {review.authenticity_flag.replace("_", " ")}
          </span>
        ) : null}
      </p>
      <p className="text-sm">
        {isDiscussion
          ? "discussion, not a star rating"
          : `★ ${review.rating} across ${(review.review_count ?? 0).toLocaleString()} reviews`}
      </p>
      {summary ? (
        <>
          <p className="mt-1 whitespace-pre-line text-sm font-normal">{shown}</p>
          {isLong ? (
            <button
              type="button"
              onClick={() => setExpanded(!expanded)}
              className="mr-3 mt-1 text-xs font-bold underline underline-offset-2"
            >
              {expanded ? "Show less" : `Show all ${summary.length.toLocaleString()} characters`}
            </button>
          ) : null}
        </>
      ) : null}
      {href ? (
        <a
          href={href}
          target="_blank"
          rel="noreferrer noopener"
          className="text-sm font-bold underline"
        >
          source
        </a>
      ) : null}
    </li>
  );
}

function ItemDetailPage() {
  const { id } = Route.useParams();
  const itemId = Number(id);
  const [item, setItem] = useState<Item | null>(null);
  const [listings, setListings] = useState<Listing[]>([]);
  const [history, setHistory] = useState<PricePoint[]>([]);
  const [reviews, setReviews] = useState<Review[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      getItem(itemId),
      getListings(itemId),
      getPriceHistory(itemId),
      getReviews(itemId),
    ])
      .then(([nextItem, nextListings, nextHistory, nextReviews]) => {
        setItem(nextItem);
        setListings(nextListings);
        setHistory(nextHistory);
        setReviews(nextReviews);
      })
      .catch((caught) => setError(caught instanceof ApiError ? caught.message : "Request failed"));
  }, [itemId]);

  const prices = history.map((point) => point.price).filter((p): p is number => p !== null);
  const chart = toChart(history, listings);

  return (
    <AppShell
      title={item?.name ?? (error ? "Item" : "Loading...")}
      subtitle={
        item
          ? `Target ${money(item.target_price)} · budget ${money(item.budget_max)} · ${item.status ?? "unknown"}`
          : undefined
      }
    >
      {error ? (
        <p className="sticker mb-4 rounded-3xl bg-strawberry px-4 py-2 text-sm font-semibold text-accent-foreground">
          {error}
        </p>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-[1.2fr_1fr]">
        <section className="panel rounded-3xl bg-card p-4">
          <h2 className="font-display text-xl font-extrabold">Price history</h2>
          <p className="text-xs font-semibold text-muted-foreground">
            One line per listing. A point is only recorded when the price changed.
          </p>
          <PriceChart chart={chart} />
          <div className="flex flex-wrap gap-2 text-xs font-bold">
            <span className="rounded-full bg-secondary px-2.5 py-1">
              best now {money(listings[0]?.price ?? null)}
            </span>
            {prices.length > 0 ? (
              <>
                <span className="rounded-full bg-butter px-2.5 py-1">
                  recorded low {money(Math.min(...prices))}
                </span>
                <span className="rounded-full bg-sky px-2.5 py-1">
                  recorded high {money(Math.max(...prices))}
                </span>
              </>
            ) : null}
          </div>
        </section>

        <section className="panel rounded-3xl bg-card p-4">
          <h2 className="font-display text-xl font-extrabold">Review signals</h2>
          <ul className="mt-3 space-y-2 text-sm font-semibold">
            {reviews.map((review) => (
              <ReviewRow key={review.id} review={review} />
            ))}
            {reviews.length === 0 ? (
              <li className="text-muted-foreground">No review data yet.</li>
            ) : null}
          </ul>
        </section>
      </div>

      <section className="panel mt-6 rounded-3xl bg-card p-4">
        <h2 className="font-display text-xl font-extrabold">Listings</h2>
        {/* the table is wider than a phone. saying it scrolls beats a cut-off column, which
            reads as a bug rather than as something you can drag */}
        <p className="text-xs text-muted-foreground sm:hidden">Scroll sideways for more →</p>
        <div className="overflow-x-auto">
        <table className="mt-3 w-full min-w-[560px] text-left text-sm font-semibold">
          <thead className="text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className="py-2">Retailer</th>
              <th>Price</th>
              <th>Stock</th>
              <th>Shipping</th>
              <th>Store</th>
              <th>Checked</th>
            </tr>
          </thead>
          <tbody>
            {listings.map((listing) => {
              const href = safeUrl(listing.url);
              return (
                <tr key={listing.id} className="border-t-2 border-border">
                  <td className="py-2">
                    {href ? (
                      <a href={href} target="_blank" rel="noreferrer noopener" className="underline">
                        {retailerLabel(listing.retailer)}
                      </a>
                    ) : (
                      retailerLabel(listing.retailer)
                    )}
                  </td>
                  <td>{money(listing.price)}</td>
                  <td>
                    <span className="rounded-full bg-secondary px-2 py-0.5 text-xs">
                      {listing.in_stock === null
                        ? "unknown"
                        : listing.in_stock
                          ? "in stock"
                          : "out of stock"}
                    </span>
                  </td>
                  <td>
                    {listing.shipping_days_est === null
                      ? "—"
                      : `${listing.shipping_days_est} days`}
                  </td>
                  <td>
                    {listing.store_name ?? "online"}
                    {listing.distance_miles !== null
                      ? ` · ${listing.distance_miles.toFixed(1)} mi`
                      : ""}
                  </td>
                  <td>{shortDateTime(listing.scraped_at)}</td>
                </tr>
              );
            })}
            {listings.length === 0 ? (
              <tr>
                <td className="py-2 text-muted-foreground" colSpan={6}>
                  No listings recorded yet.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
        </div>
      </section>
    </AppShell>
  );
}
