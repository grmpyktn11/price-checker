import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

const nav = [
  { to: "/", label: "Chat" },
  { to: "/projects", label: "Projects" },
  { to: "/watchlist", label: "Watchlist" },
  { to: "/alerts", label: "Alerts" },
  { to: "/settings", label: "Settings" },
  { to: "/how-it-works", label: "How it works" },
] as const;

export function AppShell({
  children,
  title,
  subtitle,
}: {
  children: ReactNode;
  title: string;
  subtitle?: string;
}) {
  return (
    <div className="flex min-h-screen flex-col dotgrid">
      <header className="border-b-[3px] border-foreground gingham">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-3 px-4 py-3">
          <Link to="/" className="flex items-center gap-2">
            <span className="sticker wobble grid h-10 w-10 place-items-center rounded-full bg-strawberry text-lg">
              🍏
            </span>
            <span className="font-display text-2xl font-extrabold tracking-tight">shopper</span>
          </Link>

          {/* one scrolling row on a phone, where wrapping ate a third of the first screen.
              -mx-4 px-4 lets it bleed to the screen edges so it reads as scrollable */}
          <nav className="no-scrollbar -mx-4 ml-auto flex w-full snap-x items-center gap-2 overflow-x-auto px-4 pb-1 sm:mx-0 sm:w-auto sm:flex-wrap sm:overflow-visible sm:pb-0">
            {nav.map((n) => (
              <Link
                key={n.to}
                to={n.to}
                className="sticker shrink-0 snap-start rounded-full bg-card px-3 py-1.5 text-sm font-bold transition-transform hover:-translate-y-0.5"
                activeProps={{
                  className: "sticker shrink-0 snap-start rounded-full bg-butter px-3 py-1.5 text-sm font-bold",
                }}
                activeOptions={{ exact: n.to === "/" }}
              >
                {n.label}
              </Link>
            ))}
          </nav>
        </div>
      </header>

      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-8">
        <div className="mb-6">
          <h1 className="font-display text-3xl font-extrabold tracking-tight sm:text-4xl">
            {title}
          </h1>
          {subtitle ? <p className="mt-1 text-muted-foreground">{subtitle}</p> : null}
        </div>
        {children}
      </main>

      <footer className="mt-12 border-t-[3px] border-foreground gingham-red">
        <div className="mx-auto max-w-5xl px-4 py-6 text-sm font-semibold">
          shopper — deals, tracked.
        </div>
      </footer>
    </div>
  );
}
