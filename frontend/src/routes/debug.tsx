import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";

import type { DebugStatus } from "@/api";
import { ApiError, getDebugStatus, runJob, sendTestEmail } from "@/api";
import { AppShell } from "@/components/shopper/AppShell";

export const Route = createFileRoute("/debug")({
  component: DebugPage,
});

// what each job does, in the terms someone testing it needs, plus whether it can email
const JOBS = [
  {
    id: "scrape",
    label: "Re-price watched items",
    detail: "Runs the full search again for every watched item. Records price changes and can raise alerts. Minutes.",
    schedule: "every 6 hours",
  },
  {
    id: "review_check",
    label: "Look for cheaper alternatives",
    detail: "Searches again and alerts on anything cheaper than what is already stored. Minutes.",
    schedule: "daily 03:00",
  },
  {
    id: "digest",
    label: "Send the digest email",
    detail: "Emails every alert that has not been sent yet, then marks them sent. Seconds.",
    schedule: "daily 08:00",
  },
] as const;

function DebugPage() {
  const [status, setStatus] = useState<DebugStatus | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setStatus(await getDebugStatus());
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Cannot reach the backend");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function trigger(id: string, run: () => Promise<{ detail: string }>) {
    setBusy(id);
    setNotice(null);
    setError(null);
    try {
      const result = await run();
      setNotice(result.detail);
      void load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "That failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <AppShell title="Debug" subtitle="Run the scheduled jobs now instead of waiting for them.">
      <div className="space-y-4">
        {notice ? (
          <p className="rounded-2xl bg-sky px-3 py-2 text-sm font-semibold">{notice}</p>
        ) : null}
        {error ? (
          <p className="rounded-2xl bg-strawberry px-3 py-2 text-sm font-semibold text-accent-foreground">
            {error}
          </p>
        ) : null}

        <section className="panel rounded-3xl bg-card p-4">
          <h2 className="font-display text-xl font-extrabold">State</h2>
          {status ? (
            <ul className="mt-2 space-y-1 text-sm font-semibold">
              <li>{status.watched_items} watched item{status.watched_items === 1 ? "" : "s"}</li>
              <li>
                {status.pending_alerts} alert{status.pending_alerts === 1 ? "" : "s"} queued for
                the next digest
              </li>
              <li>
                {status.email_configured
                  ? `email goes to ${status.user_email}`
                  : "email is not configured — set RESEND_API_KEY and USER_EMAIL in .env"}
              </li>
            </ul>
          ) : (
            <p className="mt-2 text-sm text-muted-foreground">Loading…</p>
          )}
          {status?.jobs.length ? (
            <ul className="mt-3 space-y-0.5 text-xs text-muted-foreground">
              {status.jobs.map((job) => (
                <li key={job.id}>
                  next {job.id}: {job.next_run ?? "not scheduled"}
                </li>
              ))}
            </ul>
          ) : null}
        </section>

        <section className="panel rounded-3xl bg-card p-4">
          <h2 className="font-display text-xl font-extrabold">Email</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Sends one message now. Proves delivery without needing an alert to exist.
          </p>
          <button
            onClick={() => void trigger("test-email", sendTestEmail)}
            disabled={busy !== null || status?.email_configured === false}
            className="sticker mt-3 rounded-full bg-primary px-4 py-2 text-sm font-extrabold text-primary-foreground disabled:opacity-50"
          >
            {busy === "test-email" ? "Sending…" : "Send test email"}
          </button>
        </section>

        {JOBS.map((job) => (
          <section key={job.id} className="panel rounded-3xl bg-card p-4">
            <div className="flex flex-wrap items-baseline gap-2">
              <h2 className="font-display text-xl font-extrabold">{job.label}</h2>
              <span className="rounded-full bg-secondary px-2 py-0.5 text-xs font-semibold">
                {job.schedule}
              </span>
            </div>
            <p className="mt-1 text-sm text-muted-foreground">{job.detail}</p>
            <button
              onClick={() => void trigger(job.id, () => runJob(job.id))}
              disabled={busy !== null}
              className="sticker mt-3 rounded-full bg-card px-4 py-2 text-sm font-extrabold disabled:opacity-50"
            >
              {busy === job.id ? "Starting…" : "Run now"}
            </button>
          </section>
        ))}
      </div>
    </AppShell>
  );
}
