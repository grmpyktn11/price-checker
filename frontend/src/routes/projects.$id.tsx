import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useRef, useState } from "react";

import type { Product, ProjectDetail, ProjectProgress } from "@/api";
import {
  ApiError,
  getProject,
  getProjectProgress,
  searchProject,
  trackProjectProduct,
} from "@/api";
import { AppShell } from "@/components/shopper/AppShell";
import { ProductCard } from "@/components/shopper/ProductCard";
import { money, retailerLabel } from "@/lib/format";

export const Route = createFileRoute("/projects/$id")({
  component: ProjectPage,
});

const POLL_MS = 1500;
// matches MAX_PROJECT_ITEMS in backend/services/project_run.py. shown, not enforced here -
// the backend caps the run and reports what it skipped
const MAX_PER_RUN = 5;

const STATE_LABEL: Record<string, string> = {
  pending: "waiting",
  searching: "searching…",
  done: "done",
  failed: "failed",
};

// the picks as something you can paste back into the Claude chat that started this
function toMarkdown(project: ProjectDetail): string {
  const lines = [`## ${project.name ?? "Project"} — what to buy`, ""];
  for (const item of project.items) {
    const picks = project.results[String(item.id)] ?? [];
    const best = picks[0];
    if (!best) continue;
    const quantity = (item.quantity ?? 1) > 1 ? ` ×${item.quantity}` : "";
    lines.push(
      `- **${item.name}**${quantity} — ${best.name ?? "a product"}, ${money(best.price)} at ` +
        `${retailerLabel(best.retailer)}${best.url ? ` — ${best.url}` : ""}`,
    );
  }
  if (lines.length === 2) lines.push("_Nothing searched yet._");
  return lines.join("\n");
}

function ProjectPage() {
  const { id } = Route.useParams();
  const projectId = Number(id);
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [progress, setProgress] = useState<ProjectProgress | null>(null);
  const [ticked, setTicked] = useState<Set<number>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [decision, setDecision] = useState<number | null>(null);
  const running = progress?.running ?? false;
  // set once the run ends, so the effect below reloads the results exactly once
  const wasRunning = useRef(false);

  const load = useCallback(async () => {
    try {
      const detail = await getProject(projectId);
      setProject(detail);
      setTicked(new Set(detail.items.filter((i) => i.selected).map((i) => i.id)));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not load that project");
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  // one poller for the whole page: it drives the progress strip, and reloading when the run
  // ends is what puts the results on screen
  useEffect(() => {
    let stopped = false;
    async function tick() {
      try {
        const next = await getProjectProgress(projectId);
        if (stopped) return;
        setProgress(next);
        if (wasRunning.current && !next.running) void load();
        wasRunning.current = next.running;
      } catch {
        /* a failed poll says nothing about the run; the next one will tell us */
      }
    }
    void tick();
    const timer = setInterval(tick, POLL_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [projectId, load]);

  function toggle(itemId: number) {
    setTicked((current) => {
      const next = new Set(current);
      if (next.has(itemId)) next.delete(itemId);
      else next.add(itemId);
      return next;
    });
  }

  async function startSearch() {
    setError(null);
    setNotice(null);
    try {
      const result = await searchProject(projectId, [...ticked]);
      setProgress({ running: true });
      wasRunning.current = true;
      if (result.skipped.length) {
        setNotice(
          `Searching ${result.searching.length}. ${result.skipped.length} left for the next run.`,
        );
      }
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not start the search");
    }
  }

  async function track(productId: number, itemId: number) {
    setDecision(productId);
    setError(null);
    try {
      const result = await trackProjectProduct(projectId, itemId, productId);
      setNotice(result.message);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not track that");
    } finally {
      setDecision(null);
    }
  }

  if (!project) {
    return (
      <AppShell title="Project">
        <p className="sticker rounded-3xl bg-card p-4 text-sm font-semibold">
          {error ?? "Loading…"}
        </p>
      </AppShell>
    );
  }

  const progressItems = progress?.items ?? [];

  return (
    <AppShell
      title={project.name ?? "Project"}
      subtitle="Pick what to search for."
    >
      <div className="space-y-6">
        <div className="sticker rounded-3xl bg-card p-4">
          <div className="flex flex-wrap items-center gap-3">
            <button
              onClick={() => void startSearch()}
              disabled={running || ticked.size === 0}
              className="sticker rounded-full bg-primary px-4 py-2 text-sm font-extrabold text-primary-foreground transition-transform hover:-translate-y-0.5 disabled:opacity-50"
            >
              {running ? "Searching…" : `Search ${ticked.size} item${ticked.size === 1 ? "" : "s"}`}
            </button>
            <button
              onClick={() => void navigator.clipboard.writeText(toMarkdown(project))}
              className="sticker rounded-full bg-card px-4 py-2 text-sm font-extrabold transition-transform hover:-translate-y-0.5"
            >
              Copy as markdown
            </button>
            <span className="text-sm text-muted-foreground">
              {MAX_PER_RUN} at a time, about a minute each.
            </span>
          </div>

          {/* one line per item while a run is in flight, so a five minute wait is legible */}
          {running && progressItems.length ? (
            <ul className="mt-3 space-y-0.5 text-sm font-semibold">
              {progressItems.map((row) => (
                <li key={row.id}>
                  {row.state === "done" ? "✓" : row.state === "failed" ? "✕" : "·"} {row.name} —{" "}
                  {STATE_LABEL[row.state] ?? row.state}
                  {row.state === "done" ? ` (${row.products_found})` : ""}
                </li>
              ))}
            </ul>
          ) : null}
          {running && progress?.current_search?.stage ? (
            <p className="mt-1 text-xs text-muted-foreground">
              {progress.current_search.stage.replace(/_/g, " ")}
            </p>
          ) : null}

          {notice ? (
            <p className="mt-3 rounded-2xl bg-sky px-3 py-2 text-sm font-semibold">{notice}</p>
          ) : null}
          {error ? (
            <p className="mt-3 rounded-2xl bg-strawberry px-3 py-2 text-sm font-semibold text-accent-foreground">
              {error}
            </p>
          ) : null}
        </div>

        {project.items.map((item) => {
          const picks: Product[] = project.results[String(item.id)] ?? [];
          return (
            <section key={item.id} className="sticker rounded-3xl bg-card p-4">
              <label className="flex cursor-pointer items-start gap-3">
                <input
                  type="checkbox"
                  checked={ticked.has(item.id)}
                  onChange={() => toggle(item.id)}
                  disabled={running}
                  className="mt-1 h-5 w-5 shrink-0"
                />
                <span className="min-w-0">
                  <span className="font-display text-lg font-bold">
                    {item.name}
                    {(item.quantity ?? 1) > 1 ? ` ×${item.quantity}` : ""}
                  </span>
                  {!item.essential ? (
                    <span className="ml-2 rounded-full bg-secondary px-2 py-0.5 text-xs font-bold">
                      optional
                    </span>
                  ) : null}
                  {item.why ? (
                    <span className="block text-sm text-muted-foreground">{item.why}</span>
                  ) : null}
                </span>
              </label>

              {item.status === "failed" ? (
                <p className="mt-2 text-sm font-semibold text-strawberry">
                  Search failed{item.error ? `: ${item.error}` : ""}
                </p>
              ) : null}

              {picks.length ? (
                <div className="mt-3 space-y-3">
                  {picks.map((product) => (
                    <ProductCard
                      key={`${item.id}-${product.product_id}`}
                      product={product}
                      onDecision={(productId) => void track(productId, item.id)}
                      pending={decision === product.product_id ? "watch" : null}
                      disabled={decision !== null}
                    />
                  ))}
                </div>
              ) : item.status === "done" ? (
                <p className="mt-2 text-sm text-muted-foreground">Nothing matched.</p>
              ) : null}
            </section>
          );
        })}
      </div>
    </AppShell>
  );
}
