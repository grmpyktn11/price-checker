import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";

import type { ConversationSummary, DebugTrace, Product } from "@/api";
import {
  ApiError,
  deleteAllConversations,
  deleteConversation,
  getConversation,
  getConversations,
  getProfile,
  getSearchProgress,
  sendDecision,
  sendMessage,
} from "@/api";
import { AppShell } from "@/components/shopper/AppShell";
import { DebugPanel } from "@/components/shopper/DebugPanel";
import { ProductCard } from "@/components/shopper/ProductCard";
import { SearchProgress } from "@/components/shopper/SearchProgress";
import { timeAgo } from "@/lib/format";

export const Route = createFileRoute("/")({ component: ChatPage });

interface Turn {
  role: string; // user | assistant
  content: string;
}

function newConversationId(): string {
  return crypto.randomUUID();
}

// so a search left running when the page unmounts can be found again on the way back
const LAST_CONVERSATION_KEY = "shopper.last-conversation";

function ChatPage() {
  const [conversationId, setConversationId] = useState(newConversationId);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [debug, setDebug] = useState<DebugTrace | null>(null);
  const [draft, setDraft] = useState("");
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [noLocation, setNoLocation] = useState(false);
  const [decision, setDecision] = useState<{ id: number; kind: "buy_now" | "watch" } | null>(null);
  const [past, setPast] = useState<ConversationSummary[]>([]);
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    void refreshPast();
    // searches work without a location, just with distance scored neutral - worth a nudge
    getProfile()
      .then((profile) => setNoLocation(profile.lat == null || profile.lon == null))
      .catch(() => {});
    const saved = localStorage.getItem(LAST_CONVERSATION_KEY);
    if (saved) void resumeIfRunning(saved);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "nearest" });
  }, [turns.length, searching]);

  async function refreshPast() {
    // a missing list is not worth an error banner: the transcript still works
    setPast(await getConversations().catch(() => []));
  }

  // the backend evicts conversations, so a 404 means starting over rather than retrying
  function resetConversation(message: string) {
    setConversationId(newConversationId());
    setTurns([]);
    setProducts([]);
    setDebug(null);
    setNotice(message);
  }

  // deleting the open conversation would leave the transcript on screen pointing at a row
  // that no longer exists, so that case resets the page too
  async function forget(doomed: string) {
    setError(null);
    try {
      await deleteConversation(doomed);
      if (doomed === conversationId) resetConversation("Deleted that conversation.");
      else setNotice("Deleted that conversation.");
      void refreshPast();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not delete that");
    }
  }

  async function forgetAll() {
    setError(null);
    try {
      const result = await deleteAllConversations();
      resetConversation(`Cleared ${result.deleted} conversations.`);
      void refreshPast();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not clear those");
    }
  }

  async function send() {
    const message = draft.trim();
    if (!message || searching) return;
    setDraft("");
    setError(null);
    setNotice(null);
    setTurns((current) => [...current, { role: "user", content: message }]);
    setSearching(true);
    localStorage.setItem(LAST_CONVERSATION_KEY, conversationId);
    try {
      const response = await sendMessage(conversationId, message);
      if (response.type === "followup") {
        setTurns((current) => [...current, { role: "assistant", content: response.question }]);
      } else {
        setTurns((current) => [...current, { role: "assistant", content: response.narration }]);
        setProducts(response.products);
        setDebug(response.debug ?? null);
      }
      void refreshPast();
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 404) {
        resetConversation("That conversation expired. Started a fresh one — send it again.");
      } else {
        setError(caught instanceof ApiError ? caught.message : "Request failed");
      }
    } finally {
      setSearching(false);
    }
  }

  async function decide(productId: number, kind: "buy_now" | "watch") {
    setDecision({ id: productId, kind });
    setError(null);
    try {
      const response = await sendDecision(conversationId, productId, kind);
      setNotice(response.message);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 404) {
        resetConversation("That conversation expired, so those picks are gone. Search again.");
      } else {
        setError(caught instanceof ApiError ? caught.message : "Request failed");
      }
    } finally {
      setDecision(null);
    }
  }

  // the cards are stored with the conversation, and /chat/decision reads its own stored
  // records, so a reopened conversation's buy/track buttons genuinely work
  async function restore(id: string) {
    setError(null);
    try {
      const conversation = await getConversation(id);
      setConversationId(conversation.id);
      setTurns(conversation.history);
      setProducts(conversation.products ?? []);
      setDebug(null);
      setNotice("Reopened.");
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Request failed");
    }
  }

  // a search left running when this page unmounted (another tab, a reload) keeps running on
  // the server; coming back picks it up and shows the results it stored
  async function resumeIfRunning(id: string) {
    try {
      if (!(await getSearchProgress(id)).running) return;
      setConversationId(id);
      const before = await getConversation(id).catch(() => null);
      if (before) setTurns(before.history);
      const knownTurns = before?.history.length ?? 0;
      setSearching(true);
      while ((await getSearchProgress(id)).running) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
      }
      // the server writes the transcript and cards moments after the run ends (narration is
      // its own model call), so wait for the history to actually grow
      for (let poll = 0; poll < 40; poll++) {
        const done = await getConversation(id);
        if (done.history.length > knownTurns) {
          setTurns(done.history);
          setProducts(done.products ?? []);
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, 1500));
      }
    } catch {
      /* a dead conversation is just a fresh page */
    } finally {
      setSearching(false);
    }
  }

  return (
    <AppShell
      title="Search"
      subtitle="Say what you want to buy."
    >
      <div className="grid gap-6 lg:grid-cols-[1.1fr_1fr]">
        <section className="panel rounded-3xl bg-card p-4">
          <details className="mb-3">
            <summary className="cursor-pointer text-sm font-extrabold">
              Past conversations ({past.length})
            </summary>
            <ul className="mt-2 space-y-1">
              {past.map((conversation) => (
                <li key={conversation.id} className="flex items-center gap-1">
                  <button
                    onClick={() => void restore(conversation.id)}
                    className="min-w-0 flex-1 rounded-2xl bg-secondary/60 px-3 py-1.5 text-left text-sm font-semibold hover:bg-secondary"
                  >
                    {conversation.title || "(no message yet)"}
                    <span className="ml-2 text-xs text-muted-foreground">
                      {timeAgo(conversation.updated_at)}
                    </span>
                  </button>
                  <button
                    onClick={() => void forget(conversation.id)}
                    title="Delete this conversation"
                    aria-label={`Delete ${conversation.title || "conversation"}`}
                    className="shrink-0 rounded-full px-2 py-1 text-sm font-extrabold text-muted-foreground hover:text-strawberry"
                  >
                    ×
                  </button>
                </li>
              ))}
              {past.length === 0 ? (
                <li className="text-sm text-muted-foreground">Nothing here yet.</li>
              ) : null}
            </ul>
            {past.length ? (
              <button
                onClick={() => void forgetAll()}
                className="mt-2 text-xs font-bold text-muted-foreground underline"
              >
                Clear all conversations
              </button>
            ) : null}
          </details>

          <div className="space-y-3">
            {turns.map((turn, index) => (
              <div
                key={index}
                className={turn.role === "user" ? "flex justify-end" : "flex justify-start"}
              >
                <p
                  className={
                    turn.role === "user"
                      ? "sticker max-w-[85%] whitespace-pre-wrap rounded-3xl rounded-br-md bg-butter px-4 py-2.5 text-sm font-semibold"
                      : "sticker max-w-[85%] whitespace-pre-wrap rounded-3xl rounded-bl-md bg-secondary px-4 py-2.5 text-sm font-semibold"
                  }
                >
                  {turn.content}
                </p>
              </div>
            ))}
            {turns.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                Say what you want to buy, and your budget.
              </p>
            ) : null}
            {noLocation ? (
              <p className="text-sm text-muted-foreground">
                No location set, so store distance won't count.{" "}
                <Link to="/settings" className="font-bold underline">
                  Set it in Settings.
                </Link>
              </p>
            ) : null}
            {searching ? <SearchProgress conversationId={conversationId} /> : null}
            <div ref={endRef} />
          </div>

          {notice ? (
            <p className="mt-3 rounded-2xl bg-sky px-3 py-2 text-sm font-semibold">{notice}</p>
          ) : null}
          {error ? (
            <p className="mt-3 rounded-2xl bg-strawberry px-3 py-2 text-sm font-semibold text-accent-foreground">
              {error}
            </p>
          ) : null}

          <div className="mt-4 flex gap-2">
            <input
              value={draft}
              disabled={searching}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void send();
              }}
              placeholder="i want a cute desk lamp under $60..."
              className="sticker w-full min-w-0 rounded-full bg-background px-4 py-2.5 text-sm font-semibold outline-none placeholder:text-muted-foreground disabled:opacity-60"
            />
            <button
              onClick={() => void send()}
              disabled={searching}
              className="sticker shrink-0 rounded-full bg-primary px-4 py-2.5 text-sm font-extrabold text-primary-foreground transition-transform hover:-translate-y-0.5 disabled:opacity-50"
            >
              {searching ? "..." : "Send"}
            </button>
          </div>
          <button
            onClick={() => resetConversation("Started a fresh conversation.")}
            className="mt-2 text-xs font-bold text-muted-foreground underline"
          >
            New conversation
          </button>
        </section>

        <section className="space-y-4">
          <h2 className="font-display text-2xl font-extrabold">Top picks</h2>
          {products.map((product) => (
            <ProductCard
              key={product.product_id}
              product={product}
              onDecision={(id, kind) => void decide(id, kind)}
              pending={decision?.id === product.product_id ? decision.kind : null}
              disabled={decision !== null}
            />
          ))}
          {products.length === 0 ? (
            <p className="panel rounded-3xl bg-card p-4 text-sm font-semibold text-muted-foreground">
              No results yet.
            </p>
          ) : null}
        </section>
      </div>

      <DebugPanel trace={debug ?? undefined} />
    </AppShell>
  );
}
