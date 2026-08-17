import { useEffect, useState } from "react";

import type { DebugTrace } from "@/api";
import { ApiError, getLastDebug, safeUrl } from "@/api";

// the outcomes the backend records per retailer fetch. colour-coded so a failed search is
// readable at a glance rather than by reading the tree
const outcomeStyles: Record<string, string> = {
  OK: "bg-primary text-primary-foreground",
  OK_BUT_EMPTY: "bg-butter",
  SELECTORS_RETURNED_NOTHING: "bg-sky",
  BLOCKED: "bg-strawberry text-accent-foreground",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// the trace shape is still moving, so outcomes are found by value anywhere in the tree
// instead of at a fixed path
function collectOutcomes(node: unknown, found: string[] = []): string[] {
  if (typeof node === "string" && node in outcomeStyles) found.push(node);
  else if (Array.isArray(node)) node.forEach((entry) => collectOutcomes(entry, found));
  else if (isRecord(node)) Object.values(node).forEach((entry) => collectOutcomes(entry, found));
  return found;
}

function OutcomePill({ value }: { value: string }) {
  return (
    <span
      className={`sticker rounded-full px-2 py-0.5 text-xs font-extrabold ${outcomeStyles[value] ?? "bg-secondary"}`}
    >
      {value}
    </span>
  );
}

function Scalar({ value }: { value: unknown }) {
  if (typeof value === "string" && value in outcomeStyles) return <OutcomePill value={value} />;
  const href = typeof value === "string" ? safeUrl(value) : null;
  if (href) {
    return (
      <a href={href} target="_blank" rel="noreferrer noopener" className="break-all underline">
        {href}
      </a>
    );
  }
  return <span className="break-all">{JSON.stringify(value)}</span>;
}

// every branch is a native <details>, so the whole tree is collapsed until it is opened
// and no expansion state has to be tracked
function Node({ label, value }: { label: string; value: unknown }) {
  if (!isRecord(value) && !Array.isArray(value)) {
    return (
      <div className="flex flex-wrap gap-2 py-0.5">
        <span className="font-bold">{label}</span>
        <Scalar value={value} />
      </div>
    );
  }
  const entries = Array.isArray(value)
    ? value.map((entry, index) => [String(index), entry] as const)
    : Object.entries(value);
  const outcomes = collectOutcomes(value);
  return (
    <details className="py-0.5" open={entries.length <= 8}>
      <summary className="cursor-pointer font-bold">
        {label}{" "}
        <span className="font-normal text-muted-foreground">
          {Array.isArray(value) ? `[${entries.length}]` : `{${entries.length}}`}
        </span>{" "}
        {[...new Set(outcomes)].map((outcome) => (
          <OutcomePill key={outcome} value={outcome} />
        ))}
      </summary>
      <div className="ml-3 border-l-2 border-border pl-3">
        {entries.map(([key, entry]) => (
          <Node key={key} label={key} value={entry} />
        ))}
      </div>
    </details>
  );
}

export function DebugPanel({ trace }: { trace: DebugTrace | null }) {
  const [open, setOpen] = useState(false);
  const [fetched, setFetched] = useState<DebugTrace | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const shown = fetched ?? trace;
  const outcomes = shown ? collectOutcomes(shown) : [];

  // a fresh search supersedes whatever Load last put here, including its error
  useEffect(() => {
    setFetched(null);
    setError(null);
  }, [trace]);

  async function loadLast() {
    setError(null);
    try {
      setFetched(await getLastDebug());
    } catch (caught) {
      setError(
        caught instanceof ApiError && caught.status === 404
          ? "No trace recorded yet (GET /api/debug/last returned 404)."
          : caught instanceof ApiError
            ? caught.message
            : "Request failed"
      );
    }
  }

  function copy() {
    if (!shown) return;
    void navigator.clipboard.writeText(JSON.stringify(shown, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <section className="sticker mt-6 rounded-3xl bg-card p-4">
      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={() => setOpen(!open)}
          className="text-sm font-extrabold"
          aria-expanded={open}
        >
          {open ? "▾" : "▸"} Debug trace
        </button>
        {[...new Set(outcomes)].map((outcome) => (
          <OutcomePill key={outcome} value={outcome} />
        ))}
        <div className="ml-auto flex gap-2">
          <button
            onClick={loadLast}
            className="sticker rounded-full bg-card px-3 py-1 text-xs font-extrabold"
          >
            Load last
          </button>
          <button
            onClick={copy}
            disabled={!shown}
            className="sticker rounded-full bg-butter px-3 py-1 text-xs font-extrabold disabled:opacity-50"
          >
            {copied ? "Copied" : "Copy JSON"}
          </button>
        </div>
      </div>

      {open ? (
        <div className="mt-3 overflow-x-auto text-xs">
          {error ? <p className="font-semibold text-destructive">{error}</p> : null}
          {shown ? (
            Object.entries(shown).map(([key, value]) => (
              <Node key={key} label={key} value={value} />
            ))
          ) : (
            <p className="text-muted-foreground">
              No trace yet. Run a search, or press Load last to read the most recent one.
            </p>
          )}
        </div>
      ) : null}
    </section>
  );
}
