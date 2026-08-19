import { useState } from "react";
import type { Product } from "@/api";
import { safeUrl } from "@/api";
import { isInherited, money, retailerLabel } from "@/lib/format";

// what a source row is called on the card. anything else shows its raw name
const SOURCE_LABEL: Record<string, string> = {
  reddit: "Reddit",
  youtube: "YouTube",
};

function sourceLabel(source: string | null): string {
  if (!source) return "unknown";
  const base = source.replace(/_inherited$/, "");
  return SOURCE_LABEL[base] ?? retailerLabel(base);
}

// the ranking weights, so the breakdown reads as "this is why it scored what it scored"
const breakdown = [
  { key: "spec_match", label: "specs" },
  { key: "review_score", label: "reviews" },
  { key: "price_score", label: "price" },
  { key: "distance_score", label: "distance" },
  { key: "nice_to_have_score", label: "extras" },
] as const;

export function ProductCard({
  product,
  onDecision,
  pending,
  disabled,
}: {
  product: Product;
  onDecision: (productId: number, decision: "buy_now" | "watch") => void;
  pending: "buy_now" | "watch" | null;
  disabled: boolean;
}) {
  const [showSources, setShowSources] = useState(false);
  const href = safeUrl(product.url);
  const buyLink = href;
  // scraped/model-supplied url, so it goes through the same guard as the listing
  const videoLink = safeUrl(product.video_url ?? null);
  // the API has no per-product "was" price, so there is no strikethrough here: price history
  // only exists for watched listings and lives on the item detail page
  return (
    <article className="sticker relative rounded-3xl bg-card p-4">
      <span className="sticker absolute -right-2 -top-3 rotate-3 rounded-full bg-butter px-3 py-1 text-xs font-extrabold">
        match {Math.round(product.final_score * 100)}%
      </span>

      <div className="min-w-0">
        <h3 className="font-display text-lg font-bold leading-tight break-words">
          {product.name ?? "Unnamed listing"}
        </h3>
        <p className="text-sm text-muted-foreground">
          {retailerLabel(product.retailer)}
          {" · "}
          {product.in_stock === null ? "stock unknown" : product.in_stock ? "in stock" : "out of stock"}
          {product.distance_miles !== null ? ` · ${product.distance_miles.toFixed(1)} mi` : ""}
        </p>
      </div>

      <div className="mt-3 flex flex-wrap items-baseline gap-2">
        <span className="font-display text-3xl font-extrabold">{money(product.price)}</span>
        {href ? (
          <a
            href={href}
            target="_blank"
            rel="noreferrer noopener"
            className="text-sm font-bold underline"
          >
            open listing
          </a>
        ) : (
          <span className="text-sm font-semibold text-muted-foreground">no link</span>
        )}
      </div>

      <ul className="mt-3 flex flex-wrap gap-1.5">
        {breakdown.map((part) => (
          <li
            key={part.key}
            className="rounded-full bg-secondary px-2.5 py-1 text-xs font-semibold"
          >
            {part.label} {Math.round(product[part.key] * 100)}%
          </li>
        ))}
      </ul>

      <p className="mt-3 text-sm font-semibold">
        {product.rating === null
          ? "no rating found"
          : `★ ${product.rating} · ${(product.review_count ?? 0).toLocaleString()} reviews`}
      </p>

      {/* a rating or spec set attributed from another retailer's listing is not this
          retailer's own data, so it is labelled rather than shown bare */}
      {isInherited(product.rating_source) ? (
        <p className="mt-1 text-xs font-bold text-muted-foreground">
          rating borrowed from {retailerLabel(product.rating_source)}
        </p>
      ) : null}
      {product.specs_inherited_from ? (
        <p className="text-xs font-bold text-muted-foreground">
          specs borrowed from {retailerLabel(product.specs_inherited_from)}
        </p>
      ) : null}

      {/* the same product in another colour, or at another retailer. collapsed into this card
          so one product is recommended once, but the shopper still gets to see the option */}
      {product.variants.length ? (
        <ul className="mt-2 space-y-0.5 text-xs font-semibold">
          {product.variants.map((variant, index) => {
            const link = safeUrl(variant.url);
            return (
              <li key={index} className="text-muted-foreground">
                also{" "}
                {link ? (
                  <a href={link} target="_blank" rel="noreferrer noopener" className="underline">
                    {variant.name ?? "another option"}
                  </a>
                ) : (
                  (variant.name ?? "another option")
                )}{" "}
                — {money(variant.price)} at {retailerLabel(variant.retailer)}
              </li>
            );
          })}
        </ul>
      ) : null}

      {/* the evidence the score was built from, so it can be checked rather than trusted */}
      {product.sources.length ? (
        <div className="mt-3">
          <button
            type="button"
            onClick={() => setShowSources(!showSources)}
            className="text-xs font-bold underline underline-offset-4"
          >
            {showSources ? "Hide" : "What the sources say"} ({product.sources.length})
          </button>
          {showSources ? (
            <ul className="mt-2 space-y-2">
              {product.sources.map((row, index) => {
                const link = safeUrl(row.url);
                return (
                  <li key={index} className="rounded-2xl bg-secondary px-3 py-2 text-xs">
                    <p className="font-bold">
                      {sourceLabel(row.source)}
                      {row.rating !== null ? ` · ★ ${row.rating}` : ""}
                      {row.review_count ? ` · ${row.review_count.toLocaleString()} reviews` : ""}
                      {row.mention_count ? ` · ${row.mention_count} threads` : ""}
                      {isInherited(row.source) ? " · borrowed" : ""}
                    </p>
                    {row.summary ? (
                      <p className="mt-1 whitespace-pre-line text-muted-foreground">{row.summary}</p>
                    ) : null}
                    {link ? (
                      <a
                        href={link}
                        target="_blank"
                        rel="noreferrer noopener"
                        className="mt-1 inline-block font-bold underline"
                      >
                        read it
                      </a>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          ) : null}
        </div>
      ) : null}

      {/* Buy is a plain link out; only Track writes anything. Video appears only for the
          products the research stage actually reached, so most cards show two buttons */}
      <div className="mt-4 flex flex-wrap gap-2">
        {buyLink ? (
          <a
            href={buyLink}
            target="_blank"
            rel="noreferrer"
            className="sticker flex-1 rounded-full bg-primary px-3 py-2 text-center text-sm font-extrabold text-primary-foreground transition-transform hover:-translate-y-0.5"
          >
            Buy now
          </a>
        ) : (
          <span className="flex-1 rounded-full px-3 py-2 text-center text-sm font-bold text-muted-foreground">
            no link
          </span>
        )}
        {videoLink ? (
          <a
            href={videoLink}
            target="_blank"
            rel="noreferrer"
            className="sticker flex-1 rounded-full bg-sky px-3 py-2 text-center text-sm font-extrabold transition-transform hover:-translate-y-0.5"
          >
            Video
          </a>
        ) : null}
        <button
          disabled={disabled}
          onClick={() => onDecision(product.product_id, "watch")}
          className="sticker flex-1 rounded-full bg-card px-3 py-2 text-sm font-extrabold transition-transform hover:-translate-y-0.5 disabled:opacity-50"
        >
          {pending === "watch" ? "..." : "Track"}
        </button>
      </div>
    </article>
  );
}
