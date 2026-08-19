import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";

import type { DebugStatus } from "@/api";
import { ApiError, getDebugStatus, runJob, sendTestEmail } from "@/api";
import { AppShell } from "@/components/shopper/AppShell";
import { Link } from "@tanstack/react-router";

export const Route = createFileRoute("/debug")({
  component: DebugPage,
});

// slow ones re-run the pipeline per watched item and return as soon as they start
const JOBS = [
  { id: "scrape", schedule: "interval 6h", note: "re-prices every item · async" },
  { id: "review_check", schedule: "cron 03:00", note: "cheaper alternatives · async" },
  { id: "digest", schedule: "cron 08:00", note: "sends pending alerts · sync" },
] as const;

function DebugPage() {
  const [status, setStatus] = useState<DebugStatus | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [log, setLog] = useState<string[]>([]);

  const load = useCallback(async () => {
    try {
      setStatus(await getDebugStatus());
    } catch (caught) {
      append(caught instanceof ApiError ? `status ${caught.status}` : "backend unreachable");
    }
  }, []);

  function append(line: string) {
    const at = new Date().toLocaleTimeString();
    setLog((current) => [`${at}  ${line}`, ...current].slice(0, 20));
  }

  useEffect(() => {
    void load();
  }, [load]);

  async function fire(id: string, run: () => Promise<{ detail: string }>) {
    setBusy(id);
    append(`${id} ...`);
    try {
      const result = await run();
      append(`${id} -> ${result.detail}`);
      void load();
    } catch (caught) {
      append(`${id} -> ${caught instanceof ApiError ? `${caught.status} ${caught.message}` : "failed"}`);
    } finally {
      setBusy(null);
    }
  }

  const rows: [string, string][] = status
    ? [
        ["watched items", String(status.watched_items)],
        ["alerts pending", String(status.pending_alerts)],
        ["email", status.email_configured ? (status.user_email ?? "?") : "NOT CONFIGURED"],
        ...status.jobs.map((job): [string, string] => [
          `next ${job.id}`,
          job.next_run ?? "unscheduled",
        ]),
      ]
    : [];

  return (
    <AppShell title="Debug" subtitle="POST /api/debug/*">
      <div className="space-y-4 font-mono text-sm">
        <Link to="/email-preview" className="inline-block font-bold underline underline-offset-4">
          email-preview
        </Link>
        <section className="panel rounded-2xl bg-card p-3">
          <table className="w-full tabular-nums">
            <tbody>
              {rows.map(([key, value]) => (
                <tr key={key} className="border-b border-foreground/10 last:border-0">
                  <td className="py-1 pr-4 text-muted-foreground">{key}</td>
                  <td className="py-1 break-all">{value}</td>
                </tr>
              ))}
              {rows.length === 0 ? (
                <tr>
                  <td className="py-1 text-muted-foreground">loading</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </section>

        <section className="panel rounded-2xl bg-card p-3">
          <table className="w-full">
            <tbody>
              <tr className="border-b border-foreground/10">
                <td className="py-1.5 pr-3">
                  <button
                    onClick={() => void fire("test-email", sendTestEmail)}
                    disabled={busy !== null}
                    className="sticker rounded px-2 py-0.5 text-xs font-bold disabled:opacity-40"
                  >
                    run
                  </button>
                </td>
                <td className="py-1.5 pr-3 font-bold">test-email</td>
                <td className="py-1.5 pr-3 text-muted-foreground">—</td>
                <td className="py-1.5 text-muted-foreground">one message to the alert address</td>
              </tr>
              {JOBS.map((job) => (
                <tr key={job.id} className="border-b border-foreground/10 last:border-0">
                  <td className="py-1.5 pr-3">
                    <button
                      onClick={() => void fire(job.id, () => runJob(job.id))}
                      disabled={busy !== null}
                      className="sticker rounded px-2 py-0.5 text-xs font-bold disabled:opacity-40"
                    >
                      run
                    </button>
                  </td>
                  <td className="py-1.5 pr-3 font-bold">{job.id}</td>
                  <td className="py-1.5 pr-3 text-muted-foreground">{job.schedule}</td>
                  <td className="py-1.5 text-muted-foreground">{job.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className="panel rounded-2xl bg-card p-3">
          <p className="mb-1 text-xs uppercase tracking-wide text-muted-foreground">output</p>
          {log.length === 0 ? (
            <p className="text-muted-foreground">—</p>
          ) : (
            <ul className="space-y-0.5">
              {log.map((line, index) => (
                <li key={index} className="break-all">
                  {line}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </AppShell>
  );
}
