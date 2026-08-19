import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { ApiError, getEmailPreview } from "@/api";
import { AppShell } from "@/components/shopper/AppShell";

export const Route = createFileRoute("/email-preview")({
  component: EmailPreviewPage,
});

function EmailPreviewPage() {
  const [preview, setPreview] = useState<{ subject: string; html: string; live: boolean } | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getEmailPreview()
      .then(setPreview)
      .catch((caught) =>
        setError(caught instanceof ApiError ? caught.message : "Cannot reach the backend"),
      );
  }, []);

  return (
    <AppShell
      title="Email preview"
      subtitle={preview?.live ? "Your queued alerts." : "Sample alerts. Nothing is queued."}
    >
      {error ? (
        <p className="rounded-2xl bg-strawberry px-3 py-2 text-sm font-semibold text-accent-foreground">
          {error}
        </p>
      ) : null}

      {preview ? (
        <div className="panel overflow-hidden rounded-3xl bg-card">
          <div className="border-b-2 border-foreground/10 px-5 py-4">
            <p className="font-display text-lg font-bold">{preview.subject}</p>
            <p className="text-sm text-muted-foreground">
              Shopper &lt;onboarding@resend.dev&gt; to me
            </p>
          </div>
          {/* an iframe, not dangerouslySetInnerHTML: the email carries its own inline styles
              and must render exactly as a mail client would, without the app's css reaching it */}
          <iframe
            title="Email body"
            srcDoc={preview.html}
            sandbox=""
            className="h-[560px] w-full border-0 bg-white"
          />
        </div>
      ) : null}
    </AppShell>
  );
}
