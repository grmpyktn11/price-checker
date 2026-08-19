import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import type { Project } from "@/api";
import { ApiError, getProjects, importProject } from "@/api";
import { AppShell } from "@/components/shopper/AppShell";

export const Route = createFileRoute("/projects/")({
  head: () => ({
    meta: [
      { title: "Projects" },
      {
        name: "description",
        content: "Import a Claude conversation and shop for everything it said you need.",
      },
    ],
  }),
  component: ProjectsPage,
});

// a share link is a url; anything else is treated as the conversation itself. the backend
// checks the host properly - this only decides which field to send
function isShareLink(value: string): boolean {
  return /^https?:\/\/\S+$/i.test(value.trim());
}

function ProjectsPage() {
  const navigate = useNavigate();
  const [draft, setDraft] = useState("");
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);

  useEffect(() => {
    void getProjects()
      .then(setProjects)
      .catch(() => setProjects([]));
  }, []);

  async function submit() {
    const value = draft.trim();
    if (!value || importing) return;
    setImporting(true);
    setError(null);
    try {
      const project = await importProject(
        isShareLink(value) ? { share_url: value } : { text: value },
      );
      void navigate({ to: "/projects/$id", params: { id: String(project.id) } });
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Import failed");
    } finally {
      setImporting(false);
    }
  }

  return (
    <AppShell
      title="Projects"
      subtitle="Planned something with Claude? Bring the shopping list here."
    >
      <div className="space-y-6">
        <div className="sticker rounded-3xl bg-card p-5">
          <label htmlFor="transcript" className="font-display text-lg font-bold">
            Paste the conversation, or a share link
          </label>
          <p className="mt-1 text-sm text-muted-foreground">
            In Claude, hit <strong>Share</strong> and paste the link here — or just select the
            chat and paste the text. Shopper pulls out the things you would have to buy.
          </p>
          <textarea
            id="transcript"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            rows={8}
            disabled={importing}
            placeholder="https://claude.ai/share/…    or paste the whole conversation"
            className="sticker mt-3 w-full rounded-2xl bg-background px-3 py-2 text-sm disabled:opacity-60"
          />
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <button
              onClick={() => void submit()}
              disabled={importing || !draft.trim()}
              className="sticker rounded-full bg-primary px-4 py-2 text-sm font-extrabold text-primary-foreground transition-transform hover:-translate-y-0.5 disabled:opacity-50"
            >
              {importing ? "Reading it…" : "Import"}
            </button>
            {importing ? (
              <span className="text-sm font-semibold text-muted-foreground">
                One model call, a few seconds.
              </span>
            ) : null}
          </div>
          {error ? (
            <p className="mt-3 rounded-2xl bg-strawberry px-3 py-2 text-sm font-semibold text-accent-foreground">
              {error}
            </p>
          ) : null}
        </div>

        <section>
          <h2 className="font-display mb-3 text-2xl font-extrabold">Your projects</h2>
          {projects.length === 0 ? (
            <p className="sticker rounded-3xl bg-card p-4 text-sm font-semibold text-muted-foreground">
              Nothing imported yet.
            </p>
          ) : (
            <ul className="space-y-2">
              {projects.map((project) => (
                <li key={project.id}>
                  <Link
                    to="/projects/$id"
                    params={{ id: String(project.id) }}
                    className="sticker block rounded-3xl bg-card p-4 transition-transform hover:-translate-y-0.5"
                  >
                    <span className="font-display text-lg font-bold">
                      {project.name ?? "Untitled project"}
                    </span>
                    <span className="ml-2 text-sm text-muted-foreground">
                      {project.source === "share_link" ? "from a share link" : "pasted"}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </AppShell>
  );
}
