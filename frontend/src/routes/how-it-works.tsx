import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import type { ReactNode } from "react";

import { AppShell } from "@/components/shopper/AppShell";

export const Route = createFileRoute("/how-it-works")({
  head: () => ({
    meta: [
      { title: "How Shopper works" },
      { name: "description", content: "The short version, or the whole pipeline." },
    ],
  }),
  component: HowItWorksPage,
});

// the full walkthrough is a standalone page in public/, not a react route: it is a document
// with its own typography, and wrapping it in the app shell would fight both
const LONG_URL = "/pipeline.html";

// the order is the point: cheap work first, expensive research only on what survives
const steps = [
  "You describe what you want. If something important is missing, it asks one question first.",
  "Best Buy, Target, Amazon and Micro Center get searched at the same time.",
  "Star ratings come off the search page itself. The best listings also get their product pages read, for specs.",
  "One pass decides what actually qualifies. A number you stated is strict - a 2,000mAh charger does not survive a 20,000mAh request.",
  "The top five get their own Reddit search, so the reviews are about that exact product. Every card can show you what those sources actually said.",
  "If the top two are too close to call, YouTube reviews are pulled in to break the tie.",
  "Everything is ranked and written up from the real numbers. The same product in two colours is shown once, with the other colour listed on its card.",
];

const weights = [
  { label: "Matches your specs", value: 35 },
  { label: "Reviews", value: 25 },
  { label: "Price", value: 20 },
  { label: "Store nearby", value: 10 },
  { label: "Nice-to-haves", value: 10 },
];

const limits = [
  "Distance is per retailer, not per product. Shopper knows a Best Buy is 0.6 miles away, not whether that store has the item on a shelf.",
  "Retailers block us sometimes. That gets reported, never worked around - open the debug panel on the chat page to see which one and why.",
  "A rating borrowed from the same product at another retailer is labelled as borrowed.",
  "Prices are from the moment of the search. Tracked items are re-checked every six hours.",
];

function Card({ children }: { children: ReactNode }) {
  return <div className="panel rounded-3xl bg-card p-5">{children}</div>;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h2 className="font-display mb-3 text-2xl font-extrabold">{title}</h2>
      {children}
    </section>
  );
}

function HowItWorksPage() {
  const [short, setShort] = useState(false);

  if (!short) {
    return (
      <AppShell title="How Shopper works" subtitle="Pick one." align="center">
        {/* deliberately unlabelled: two buttons that only say how long it takes reads better
            than two paragraphs explaining which one to want. both carry the same weight
            because neither is the recommended one */}
        <div className="flex flex-wrap justify-center gap-3 py-16">
          <button
            onClick={() => setShort(true)}
            className="sticker rounded-full bg-card px-5 py-2.5 text-sm font-extrabold transition-transform hover:-translate-y-0.5"
          >
            Short way
          </button>
          <a
            href={LONG_URL}
            className="sticker rounded-full bg-card px-5 py-2.5 text-sm font-extrabold transition-transform hover:-translate-y-0.5"
          >
            Long way
          </a>
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell title="How Shopper works" subtitle="The short version.">
      <div className="space-y-8">
        <div className="flex flex-wrap items-center gap-4">
          <button
            onClick={() => setShort(false)}
            className="text-sm font-bold underline underline-offset-4"
          >
            Back
          </button>
          <a href={LONG_URL} className="text-sm font-bold underline underline-offset-4">
            Long way instead
          </a>
        </div>

        <Card>
          <p className="text-base">
            Shopper <strong>searches</strong> four retailers, researches the best candidates and
            ranks them. Anything you <strong>track</strong> gets re-checked every six hours, and
            you get an email when the price drops.
          </p>
          <p className="mt-2 text-base">
            Planned a project with Claude? Paste the conversation into <strong>Projects</strong>
            {" "}and Shopper pulls out the shopping list, then goes and finds each thing.
          </p>
        </Card>

        <Section title="A search, step by step">
          <ol className="space-y-2">
            {steps.map((step, index) => (
              <li key={step} className="panel flex items-start gap-3 rounded-3xl bg-card p-4">
                <span className="sticker grid h-8 w-8 shrink-0 place-items-center rounded-full bg-butter text-sm font-extrabold">
                  {index + 1}
                </span>
                <p className="min-w-0 text-sm">{step}</p>
              </li>
            ))}
          </ol>
          <p className="mt-3 text-sm text-muted-foreground">
            About 80 seconds, almost all of it waiting on the retailers.
          </p>
        </Section>

        <Section title="How the score is built">
          <Card>
            <ul className="space-y-2">
              {weights.map((weight) => (
                <li key={weight.label} className="flex items-center gap-3">
                  <span className="w-12 shrink-0 text-right font-display text-lg font-extrabold tabular-nums">
                    {weight.value}%
                  </span>
                  {/* the bar is the number, so it needs no separate legend */}
                  <span
                    className="h-3 rounded-full bg-sky"
                    style={{ width: `${weight.value * 2}%` }}
                    aria-hidden
                  />
                  <span className="text-sm font-semibold">{weight.label}</span>
                </li>
              ))}
            </ul>
            <p className="mt-3 text-sm text-muted-foreground">
              Every result card shows this breakdown. Going over budget costs points rather than
              disqualifying, so a pricier option that is clearly better still wins.
            </p>
          </Card>
        </Section>

        <Section title="What it cannot do">
          <Card>
            <ul className="space-y-2 text-sm">
              {limits.map((limit) => (
                <li key={limit} className="flex gap-2">
                  <span aria-hidden>·</span>
                  <span>{limit}</span>
                </li>
              ))}
            </ul>
          </Card>
        </Section>
      </div>
    </AppShell>
  );
}
