import { useEffect, useState } from "react";
import type { SearchProgress as Progress } from "@/api";
import { getSearchProgress } from "@/api";
import { retailerLabel } from "@/lib/format";

const POLL_MS = 900;

// the pipeline's stage names, in the order they run, with what to say while each one is in
// flight. a stage missing from here still shows - it just gets its raw name
const STAGES: { key: string; label: string }[] = [
  { key: "collect_candidates", label: "Searching Best Buy, Target, Amazon and Micro Center" },
  { key: "amazon_review_tiles", label: "Looking up the missing star ratings" },
  { key: "product_filter", label: "Working out which ones actually match" },
  { key: "lookup_missing_reviews", label: "Filling in the last of the ratings" },
  { key: "research_top", label: "Researching the top 5 on Reddit" },
  { key: "research_reddit", label: "Researching the top 5 on Reddit" },
  { key: "research_youtube", label: "Too close to call - checking YouTube reviews" },
];

function stageLabel(stage: string | null | undefined): string {
  if (!stage) return "Starting the search";
  return STAGES.find((s) => s.key === stage)?.label ?? stage.replace(/_/g, " ");
}

// how far through the run we are, by position in STAGES. purely for the bar; the text above
// it is the real information
function stageIndex(stage: string | null | undefined): number {
  const found = STAGES.findIndex((s) => s.key === stage);
  return found < 0 ? 0 : found;
}

const OUTCOME_MARK: Record<string, string> = {
  OK: "✓",
  OK_BUT_EMPTY: "—",
  BLOCKED: "✕",
  SELECTORS_RETURNED_NOTHING: "✕",
  ERROR: "✕",
};

export function SearchProgress({ conversationId }: { conversationId: string }) {
  const [progress, setProgress] = useState<Progress | null>(null);

  useEffect(() => {
    let stopped = false;
    // a failed poll is not worth surfacing: the search itself is still running and its own
    // response is what reports success or failure
    async function tick() {
      try {
        const next = await getSearchProgress(conversationId);
        if (!stopped && next.running) setProgress(next);
      } catch {
        /* ignore */
      }
    }
    void tick();
    const timer = setInterval(tick, POLL_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [conversationId]);

  const seconds = Math.round((progress?.elapsed_ms ?? 0) / 1000);
  const retailers = progress?.retailers ?? [];
  const percent = Math.min(95, ((stageIndex(progress?.stage) + 1) / STAGES.length) * 100);

  return (
    <div className="flex justify-start">
      <div className="sticker max-w-[85%] rounded-3xl rounded-bl-md bg-secondary px-4 py-3">
        <p className="text-sm font-bold">
          <span className="wobble inline-block">🍏</span> {stageLabel(progress?.stage)}…
          {seconds ? <span className="font-semibold text-muted-foreground"> {seconds}s</span> : null}
        </p>

        <div className="mt-2 h-2 w-64 max-w-full overflow-hidden rounded-full bg-card">
          <div
            className="h-full rounded-full bg-sky transition-[width] duration-500"
            style={{ width: `${percent}%` }}
          />
        </div>

        {/* each retailer appears the moment its search returns, with what it actually did */}
        {retailers.length ? (
          <ul className="mt-2 space-y-0.5 text-xs font-semibold">
            {retailers.map((row) => (
              <li key={row.retailer}>
                {OUTCOME_MARK[row.outcome] ?? "·"} {retailerLabel(row.retailer)}
                {row.outcome === "OK"
                  ? ` — ${row.candidates_kept ?? 0} kept`
                  : ` — ${row.outcome.toLowerCase().replace(/_/g, " ")}`}
              </li>
            ))}
          </ul>
        ) : null}

        {progress?.qualified != null ? (
          <p className="mt-1 text-xs font-semibold">
            ✓ {progress.qualified} of {progress.products_in} qualified
          </p>
        ) : null}
        {progress?.researched ? (
          <p className="mt-1 text-xs font-semibold">✓ {progress.researched} researched</p>
        ) : null}
      </div>
    </div>
  );
}
