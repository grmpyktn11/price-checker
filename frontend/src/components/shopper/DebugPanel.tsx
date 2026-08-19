import { useState } from "react";
import type { DebugTrace } from "@/api";
import { getLastDebug } from "@/api";

// the trace is a plain dict from the backend, so read it defensively rather than typing it
function rows(trace: DebugTrace, key: string): Record<string, unknown>[] {
  const value = trace[key];
  return Array.isArray(value) ? (value as Record<string, unknown>[]) : [];
}

function group(trace: DebugTrace, key: string): Record<string, unknown> {
  const value = trace[key];
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function text(value: unknown): string {
  if (value === null || value === undefined) return "-";
  if (typeof value === "number") return String(value);
  return String(value);
}

// what each outcome means, in the terms someone debugging actually needs
const OUTCOME_HELP: Record<string, string> = {
  OK: "answered, rows parsed",
  OK_BUT_EMPTY: "answered, genuinely no results",
  SELECTORS_RETURNED_NOTHING: "real page, parser found nothing - our bug",
  BLOCKED: "bot wall, captcha or 403 - wait it out",
  ERROR: "threw before parsing",
};

function Outcome({ value }: { value: unknown }) {
  const name = String(value ?? "");
  const bad = name === "BLOCKED" || name === "ERROR";
  const ours = name === "SELECTORS_RETURNED_NOTHING";
  const colour = bad ? "text-strawberry" : ours ? "text-foreground" : "text-muted-foreground";
  return <span className={`font-bold ${colour}`}>{name || "-"}</span>;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-4 first:mt-0">
      <p className="mb-1 text-xs font-bold uppercase tracking-wide text-muted-foreground">
        {title}
      </p>
      {children}
    </div>
  );
}

// one scrollable table; the page must never scroll sideways because of it
function Table({ head, body }: { head: string[]; body: (string | React.ReactNode)[][] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm tabular-nums">
        <thead>
          <tr className="text-xs uppercase text-muted-foreground">
            {head.map((h) => (
              <th key={h} className="pr-4 pb-1 font-semibold">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((row, i) => (
            <tr key={i} className="border-t border-foreground/15">
              {row.map((cell, j) => (
                <td key={j} className="pr-4 py-1 align-top">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function DebugPanel({ trace }: { trace?: DebugTrace }) {
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState<DebugTrace | undefined>();
  const [error, setError] = useState<string | null>(null);
  const data = trace ?? loaded;

  async function loadLast() {
    setError(null);
    try {
      setLoaded(await getLastDebug());
    } catch {
      setError("No search has run since the backend started.");
    }
  }

  const retailers = rows(data ?? {}, "retailers");
  const research = rows(data ?? {}, "research");
  const drops = rows(data ?? {}, "drops");
  const stores = group(data ?? {}, "stores");
  const filter = group(data ?? {}, "product_filter");
  const youtube = group(data ?? {}, "youtube");
  const distances = group(stores, "distance_miles");
  const stages = group(data ?? {}, "stages_ms");

  return (
    <div className="panel mt-6 rounded-3xl bg-card p-4">
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="text-sm font-bold underline underline-offset-4"
        >
          {open ? "Hide" : "Show"} search debug
        </button>
        {open ? (
          <>
            <button type="button" onClick={loadLast} className="text-sm underline underline-offset-4">
              Load last search
            </button>
            {data ? (
              <button
                type="button"
                onClick={() => navigator.clipboard.writeText(JSON.stringify(data, null, 2))}
                className="text-sm underline underline-offset-4"
              >
                Copy JSON
              </button>
            ) : null}
          </>
        ) : null}
      </div>

      {open ? (
        <div className="mt-3">
          {error ? <p className="text-sm text-strawberry">{error}</p> : null}
          {!data ? (
            <p className="text-sm text-muted-foreground">
              No trace yet. Run a search, or load the last one.
            </p>
          ) : (
            <>
              <p className="text-sm">
                {text(data.products_returned)} products returned in {text(data.total_ms)} ms
                {data.retailers_answered === false ? " - no retailer answered" : ""}
              </p>

              <Section title="Retailers">
                <Table
                  head={["retailer", "outcome", "http", "page bytes", "rows", "kept", "ms"]}
                  body={retailers.map((r) => [
                    text(r.retailer),
                    <Outcome value={r.outcome} />,
                    text(r.http_status),
                    text(r.page_chars),
                    text(r.raw_rows),
                    text(r.candidates_kept),
                    text(r.ms),
                  ])}
                />
                <ul className="mt-2 space-y-0.5 text-xs text-muted-foreground">
                  {[...new Set(retailers.map((r) => String(r.outcome ?? "")))]
                    .filter((name) => OUTCOME_HELP[name])
                    .map((name) => (
                      <li key={name}>
                        <span className="font-bold">{name}</span> - {OUTCOME_HELP[name]}
                      </li>
                    ))}
                </ul>
              </Section>

              {Object.keys(filter).length ? (
                <Section title="Filtering">
                  <p className="text-sm">
                    {text(filter.products_in)} in, {text(filter.qualified)} qualified,{" "}
                    {text(filter.rejected)} rejected ({text(filter.ms)} ms)
                  </p>
                </Section>
              ) : null}

              {drops.length ? (
                <Section title="Dropped">
                  <Table
                    head={["stage", "product", "why"]}
                    body={drops.map((d) => [
                      text(d.stage),
                      text(d.name).slice(0, 46),
                      text(d.reason),
                    ])}
                  />
                </Section>
              ) : null}

              {research.length ? (
                <Section title="Research">
                  <Table
                    head={["#", "product", "reddit posts", "youtube"]}
                    body={research.map((r) => [
                      text(r.rank),
                      text(r.name).slice(0, 46),
                      text(r.reddit_posts),
                      r.youtube ? text(r.youtube_videos) + " videos" : "not needed",
                    ])}
                  />
                  {youtube.triggered !== undefined ? (
                    <p className="mt-1 text-xs text-muted-foreground">
                      {youtube.triggered ? text(youtube.reason) : "ranking was decisive, no youtube quota spent"}
                    </p>
                  ) : null}
                </Section>
              ) : null}

              {Object.keys(distances).length ? (
                <Section title="Nearest stores">
                  <p className="text-sm">
                    {Object.entries(distances)
                      .map(([name, miles]) => `${name} ${Number(miles).toFixed(2)} mi`)
                      .join(" · ")}
                  </p>
                </Section>
              ) : null}

              {Object.keys(stages).length ? (
                <Section title="Time per stage (ms)">
                  <p className="text-sm">
                    {Object.entries(stages)
                      .sort((a, b) => Number(b[1]) - Number(a[1]))
                      .map(([name, ms]) => `${name} ${text(ms)}`)
                      .join(" · ")}
                  </p>
                </Section>
              ) : null}

              <Section title="Search urls">
                <ul className="space-y-0.5 text-xs break-all text-muted-foreground">
                  {retailers.map((r, i) => (
                    <li key={i}>
                      {text(r.retailer)}: {text(r.search_url)}
                    </li>
                  ))}
                </ul>
              </Section>
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}
