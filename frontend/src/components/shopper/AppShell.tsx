import { Link, useRouterState } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";

const nav = [
  { to: "/", label: "Chat" },
  { to: "/projects", label: "Projects" },
  { to: "/watchlist", label: "Watchlist" },
  { to: "/alerts", label: "Alerts" },
  { to: "/settings", label: "Settings" },
  { to: "/how-it-works", label: "How it works" },
  { to: "/debug", label: "Debug" },
] as const;

export function AppShell({
  children,
  title,
  subtitle,
  // pages are left-aligned; the one that is just a choice between two buttons is centred
  align = "left",
}: {
  children: ReactNode;
  title: string;
  subtitle?: string;
  align?: "left" | "center";
}) {
  const [open, setOpen] = useState(false);
  // watch the path rather than closing in a Link onClick: tapping the route you are already on
  // fires no navigation, and the drawer would stay open over the page
  const pathname = useRouterState({ select: (state) => state.location.pathname });

  useEffect(() => setOpen(false), [pathname]);

  // escape closes it, and the page behind must not scroll while the drawer covers it
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [open]);

  return (
    <div className="flex min-h-screen flex-col dotgrid">
      <header className="border-b-[3px] border-foreground gingham">
        <div className="mx-auto flex max-w-5xl items-center gap-3 px-4 py-3">
          <Link to="/" className="font-display text-2xl font-extrabold tracking-tight">
            shopper
          </Link>

          {/* the row of pills is the desktop nav; a phone gets the drawer instead */}
          <nav className="ml-auto hidden flex-wrap items-center gap-2 sm:flex">
            {nav.map((n) => (
              <Link
                key={n.to}
                to={n.to}
                className="sticker rounded-full bg-card px-3 py-1.5 text-sm font-bold transition-transform hover:-translate-y-0.5"
                activeProps={{
                  className: "sticker rounded-full bg-butter px-3 py-1.5 text-sm font-bold",
                }}
                activeOptions={{ exact: n.to === "/" }}
              >
                {n.label}
              </Link>
            ))}
          </nav>

          <button
            type="button"
            onClick={() => setOpen(true)}
            aria-label="Open menu"
            aria-expanded={open}
            aria-controls="mobile-nav"
            className="sticker ml-auto grid h-11 w-11 place-items-center rounded-full bg-card text-lg font-extrabold sm:hidden"
          >
            ☰
          </button>
        </div>
      </header>

      {/* mounted only while open, so the backdrop can never sit over the page invisibly and
          swallow taps - the bug this pattern usually ships with */}
      {open ? (
        <div className="fixed inset-0 z-50 sm:hidden">
          <button
            type="button"
            aria-label="Close menu"
            onClick={() => setOpen(false)}
            className="absolute inset-0 h-full w-full cursor-default bg-foreground/40"
          />
          <nav
            id="mobile-nav"
            className="absolute inset-y-0 right-0 flex w-72 max-w-[85vw] flex-col gap-2 overflow-y-auto border-l-[3px] border-foreground bg-card p-4"
          >
            <div className="mb-2 flex items-center justify-between">
              <span className="font-display text-xl font-extrabold">Menu</span>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="Close menu"
                className="sticker grid h-10 w-10 place-items-center rounded-full bg-card text-lg font-extrabold"
              >
                ×
              </button>
            </div>
            {nav.map((n) => (
              <Link
                key={n.to}
                to={n.to}
                className="sticker flex items-center gap-3 rounded-2xl bg-card px-4 py-3 text-base font-bold"
                activeProps={{
                  className:
                    "sticker flex items-center gap-3 rounded-2xl bg-butter px-4 py-3 text-base font-bold",
                }}
                activeOptions={{ exact: n.to === "/" }}
              >
                {n.label}
              </Link>
            ))}
          </nav>
        </div>
      ) : null}

      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-8">
        {/* the page is an application window: its title lives in the titlebar */}
        <div className="window-frame overflow-hidden rounded-xl bg-card">
          <div className="titlebar flex items-center gap-1.5 px-4 py-2">
            <h1 className="font-display flex-1 truncate text-lg tracking-wide">{title}</h1>
            <span aria-hidden="true" className="winbtn">
              —
            </span>
            <span aria-hidden="true" className="winbtn">
              □
            </span>
            <span aria-hidden="true" className="winbtn">
              ×
            </span>
          </div>
          <div className="p-4 sm:p-6">
            {subtitle ? (
              <p className={`mb-5 text-muted-foreground ${align === "center" ? "text-center" : ""}`}>
                {subtitle}
              </p>
            ) : null}
            {children}
          </div>
        </div>
      </main>

      <footer className="mt-12 border-t-[3px] border-foreground gingham-red">
        <div className="mx-auto max-w-5xl px-4 py-6 text-sm font-semibold">
          shopper
        </div>
      </footer>
    </div>
  );
}
