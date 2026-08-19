// display helpers shared by more than one page

export const dealLabel: Record<string, string> = {
  target_hit: "Target hit",
  price_drop: "Price drop",
  new_alternative: "New find",
};

const retailerLabels: Record<string, string> = {
  amazon: "Amazon",
  bestbuy: "Best Buy",
  target: "Target",
  microcenter: "Micro Center",
  reddit: "Reddit",
  youtube: "YouTube",
};

// review sources arrive as "amazon" or "amazon_title_inherited"; the retailer is the first part
export function retailerLabel(value: string | null): string {
  if (!value) return "unknown";
  const base = value.replace(/_inherited$/, "").split("_")[0] ?? value;
  return retailerLabels[base] ?? base;
}

// a source ending in _inherited was attributed from a different retailer's listing that the
// model judged to be the same product, so it is not this listing's own data
export function isInherited(source: string | null): boolean {
  return source !== null && source.endsWith("_inherited");
}

export function money(value: number | null): string {
  return value === null ? "—" : `$${value.toFixed(2)}`;
}

// backend timestamps are naive ISO strings in UTC, which Date would otherwise read as local
export function parseUtc(value: string | null): Date | null {
  if (!value) return null;
  const parsed = new Date(/[Zz+]|[+-]\d{2}:\d{2}$/.test(value) ? value : `${value}Z`);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export function timeAgo(value: string | null): string {
  const date = parseUtc(value);
  if (!date) return "never";
  const minutes = Math.round((Date.now() - date.getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hr ago`;
  return `${Math.round(hours / 24)} d ago`;
}

export function shortDateTime(value: string | null): string {
  const date = parseUtc(value);
  return date ? date.toLocaleString() : "—";
}
