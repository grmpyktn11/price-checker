import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import type { Project } from "@/api";
import { ApiError, deleteProject, getProjects, importProject } from "@/api";
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

  async function remove(projectId: number) {
    setError(null);
    try {
      await deleteProject(projectId);
      setProjects((current) => current.filter((p) => p.id !== projectId));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not delete that project");
    }
  }

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
            Paste the conversation
          </label>
          <p className="mt-1 text-sm text-muted-foreground">
            Select the chat in Claude, copy it, paste it here. Shopper pulls out the things you
            would have to buy.
          </p>
          {/* share links are accepted but claude.ai is behind Cloudflare, which serves our
              browser a bot check instead of the page. saying so up front beats letting someone
              wait 30 seconds for a failure */}
          <p className="mt-1 text-xs text-muted-foreground">
            Share links usually don't work — claude.ai answers automated requests with a bot
            check. Pasting the text always does.
          </p>
          <textarea
            id="transcript"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            rows={8}
            disabled={importing}
            placeholder="Paste the whole conversation here…"
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
                <li key={project.id} className="flex items-center gap-2">
                  <Link
                    to="/projects/$id"
                    params={{ id: String(project.id) }}
                    className="sticker block min-w-0 flex-1 rounded-3xl bg-card p-4 transition-transform hover:-translate-y-0.5"
                  >
                    <span className="font-display text-lg font-bold">
                      {project.name ?? "Untitled project"}
                    </span>
                    <span className="ml-2 text-sm text-muted-foreground">
                      {project.source === "share_link" ? "from a share link" : "pasted"}
                    </span>
                  </Link>
                  <button
                    onClick={() => void remove(project.id)}
                    aria-label={`Delete ${project.name ?? "project"}`}
                    className="sticker shrink-0 rounded-full bg-card px-3 py-2 text-sm font-extrabold text-muted-foreground hover:text-strawberry"
                  >
                    Delete
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </AppShell>
  );
}
