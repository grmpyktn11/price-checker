import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import type { Alert } from "@/api";
import { ApiError, getAlerts, safeUrl } from "@/api";
import { AppShell } from "@/components/shopper/AppShell";
import { dealLabel, money, retailerLabel, shortDateTime } from "@/lib/format";

export const Route = createFileRoute("/alerts")({ component: AlertsPage });

function AlertsPage() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getAlerts()
      .then(setAlerts)
      .catch((caught) => setError(caught instanceof ApiError ? caught.message : "Request failed"))
      .finally(() => setLoading(false));
  }, []);

  return (
    <AppShell title="Alerts" subtitle="Target hits email immediately. Everything else once a day.">
      {error ? (
        <p className="sticker mb-4 rounded-3xl bg-strawberry px-4 py-2 text-sm font-semibold text-accent-foreground">
          {error}
        </p>
      ) : null}

      <ul className="space-y-3">
        {alerts.map((alert) => {
          const href = safeUrl(alert.url);
          const reason = alert.reason ?? "";
          return (
            <li
              key={alert.id}
              className="sticker flex flex-wrap items-center gap-3 rounded-3xl bg-card p-4"
            >
              <div className="min-w-0">
                <p className="font-display text-lg font-bold leading-tight break-words">
                  {alert.item_name ?? "Unknown item"}
                </p>
                <p className="text-sm font-semibold text-muted-foreground">
                  {money(alert.price)} at {retailerLabel(alert.retailer)}
                  {href ? (
                    <>
                      {" · "}
                      <a
                        href={href}
                        target="_blank"
                        rel="noreferrer noopener"
                        className="underline"
                      >
                        open listing
                      </a>
                    </>
                  ) : null}
                </p>
              </div>
              <div className="ml-auto flex flex-wrap items-center gap-2">
                <span className="rounded-full bg-secondary px-2.5 py-1 text-xs font-extrabold">
                  {dealLabel[reason] ?? reason}
                </span>
                <span className="text-xs font-semibold text-muted-foreground">
                  {alert.sent_at ? `emailed ${shortDateTime(alert.sent_at)}` : "not emailed yet"}
                </span>
              </div>
            </li>
          );
        })}
      </ul>

      {!loading && alerts.length === 0 ? (
        <p className="sticker rounded-3xl bg-card p-4 text-sm font-semibold text-muted-foreground">
          No alerts yet. They appear when a watched price drops or hits your target.
        </p>
      ) : null}
    </AppShell>
  );
}
