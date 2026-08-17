import { createFileRoute } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { AppShell } from "@/components/shopper/AppShell";

export const Route = createFileRoute("/how-it-works")({
  head: () => ({
    meta: [
      { title: "How Shopper works" },
      {
        name: "description",
        content:
          "What happens between typing what you want and getting ranked results: the retailers, the research, the ranking, and what it cannot do.",
      },
    ],
  }),
  component: HowItWorksPage,
});

// the ordered steps of a search. numbering is real here: this is a sequence, and the order
// is the whole point - cheap work first, expensive research only on what survives
const steps: { title: string; body: ReactNode; cost?: string }[] = [
  {
    title: "You say what you want",
    body: (
      <>
        In plain language. Shopper reads the whole conversation, not just the last line, and
        pulls out the parts that matter: the product, a budget, any hard requirements, and the
        vague stuff like "compact" or "looks nice".
      </>
    ),
    cost: "1 model call",
  },
  {
    title: "It asks, if something is missing",
    body: (
      <>
        No budget, or a spec that could mean several things, and it asks one question instead of
        guessing. That is why the first message usually gets a question back rather than results.
      </>
    ),
  },
  {
    title: "Three retailers get searched",
    body: (
      <>
        Best Buy, Target and Amazon, at the same time. Best Buy and Amazon are real browser
        sessions because their pages are built by JavaScript; Target answers a plain data
        request. Up to three products from each go forward.
      </>
    ),
    cost: "3 searches, ~30s",
  },
  {
    title: "The top few get their detail pages read",
    body: (
      <>
        For specs and star ratings. Where a page has no spec table at all, the raw page text is
        read instead. Nothing else is loaded, because every page load is slow and is one more
        chance to get blocked.
      </>
    ),
  },
  {
    title: "One pass decides what actually qualifies",
    body: (
      <>
        Every listing is judged together, in one go: does it meet what you asked for, how well
        does it fit, and which of these listings are the same product sold in two places. A
        number you stated is strict, so a 2,000mAh charger does not survive a 20,000mAh request.
        Something vague like "yellow switches" is read from the title and only moves the score.
      </>
    ),
    cost: "1 model call",
  },
  {
    title: "The top five get researched individually",
    body: (
      <>
        Each one gets its own Reddit search, on its own name. This is the part that makes it
        research rather than a price list: real owners talking about that specific product, not
        a single search shared across everything.
      </>
    ),
    cost: "5 Reddit searches, free",
  },
  {
    title: "YouTube, but only on a photo finish",
    body: (
      <>
        The same pass that reads the discussion also says whether it can actually separate the
        leaders. If it can, that is the answer. If it genuinely cannot, the top two get YouTube
        reviews pulled in and get judged again with that extra evidence.
      </>
    ),
    cost: "0 quota when decisive",
  },
  {
    title: "Ranked, then explained",
    body: (
      <>
        The scores are recomputed with what the research turned up, the list is re-sorted, and
        the reply you read is written from the actual numbers. If the retailers failed, it says
        so rather than telling you the product does not exist.
      </>
    ),
    cost: "1 model call",
  },
];

const weights = [
  { label: "How well it matches your specs", value: 35 },
  { label: "Reviews and discussion", value: 25 },
  { label: "Price", value: 20 },
  { label: "Store nearby for pickup", value: 10 },
  { label: "The nice-to-haves", value: 10 },
];

const retailers = [
  {
    name: "Target",
    state: "Answers a plain data request. Fast, no browser needed. Blocks us for hours at a time, then relents.",
  },
  {
    name: "Best Buy",
    state: "Search works. Product pages are walled off, so no specs or star ratings from them - reviews come from the same product elsewhere, or from its Reddit discussion.",
  },
  {
    name: "Amazon",
    state: "Full search, specs, ratings and the star breakdown. Throttles after a burst of searches and recovers on its own.",
  },
];

const limits = [
  "Distance is per retailer, not per product. Shopper knows there is a Best Buy 0.6 miles away; it does not know whether that store has the item on a shelf.",
  "A blocked retailer is reported, never worked around. No captcha solving, no proxies. When a search comes back thin, the debug panel on the chat page says which of the three went wrong and why.",
  "Reviews borrowed from another retailer's listing are labelled as borrowed. A rating on a Best Buy card that came from Amazon says so.",
  "Prices are read at the moment of the search. Watched items are re-checked every six hours, so a tracked price is at most that stale.",
];

function Card({ children }: { children: ReactNode }) {
  return <div className="sticker rounded-3xl bg-card p-5">{children}</div>;
}

function HowItWorksPage() {
  return (
    <AppShell
      title="How Shopper works"
      subtitle="What happens between you typing a sentence and getting a ranked list."
    >
      <div className="space-y-8">
        <Card>
          <p className="text-base">
            Shopper does two things. It <strong>searches</strong> - you describe what you want,
            it checks three retailers, researches the best candidates and ranks them. Then it{" "}
            <strong>watches</strong> - anything you track gets re-checked every six hours, and
            you get an email when the price actually moves.
          </p>
        </Card>

        <section>
          <h2 className="font-display mb-3 text-2xl font-extrabold">A search, in order</h2>
          <ol className="space-y-2">
            {steps.map((step, index) => (
              <li key={step.title} className="sticker rounded-3xl bg-card p-4">
                <div className="flex items-start gap-3">
                  <span className="sticker grid h-8 w-8 shrink-0 place-items-center rounded-full bg-butter text-sm font-extrabold">
                    {index + 1}
                  </span>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-baseline gap-2">
                      <h3 className="font-display text-lg font-bold">{step.title}</h3>
                      {step.cost ? (
                        <span className="rounded-full bg-secondary px-2 py-0.5 text-xs font-semibold">
                          {step.cost}
                        </span>
                      ) : null}
                    </div>
                    <p className="mt-1 text-sm">{step.body}</p>
                  </div>
                </div>
              </li>
            ))}
          </ol>
          <p className="mt-3 text-sm text-muted-foreground">
            A search takes about thirty to sixty seconds. Almost all of that is waiting on the
            retailers, not on the thinking.
          </p>
        </section>

        <section>
          <h2 className="font-display mb-3 text-2xl font-extrabold">How the score is built</h2>
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
              Every card shows this breakdown, so a result you disagree with tells you which
              part it disagreed on. Going over budget costs points rather than disqualifying -
              a slightly pricier option that is clearly better still wins.
            </p>
          </Card>
        </section>

        <section>
          <h2 className="font-display mb-3 text-2xl font-extrabold">Where the reviews come from</h2>
          <Card>
            <p className="text-sm">
              Star ratings from the retailer, plus what people actually wrote: Reddit threads
              about that specific product, and YouTube reviews when the ranking is close. The
              discussion is also used as a cross-check - when owners contradict a high star
              rating, that counts against the product rather than being ignored.
            </p>
          </Card>
        </section>

        <section>
          <h2 className="font-display mb-3 text-2xl font-extrabold">The retailers, honestly</h2>
          <div className="grid gap-2 sm:grid-cols-3">
            {retailers.map((retailer) => (
              <div key={retailer.name} className="sticker rounded-3xl bg-card p-4">
                <h3 className="font-display text-lg font-bold">{retailer.name}</h3>
                <p className="mt-1 text-sm text-muted-foreground">{retailer.state}</p>
              </div>
            ))}
          </div>
        </section>

        <section>
          <h2 className="font-display mb-3 text-2xl font-extrabold">Tracking and alerts</h2>
          <Card>
            <p className="text-sm">
              Tracking a product saves that one listing. Every six hours it is re-checked, and a
              price change is recorded so the chart on the item page is real history rather than
              a guess. Hit your target price and the email goes out immediately; everything else
              is collected into one message a day so it is not a stream of notifications.
            </p>
          </Card>
        </section>

        <section>
          <h2 className="font-display mb-3 text-2xl font-extrabold">What it cannot do</h2>
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
        </section>
      </div>
    </AppShell>
  );
}
