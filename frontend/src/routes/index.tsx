import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";

import type { ConversationSummary, DebugTrace, Product } from "@/api";
import {
  ApiError,
  getConversation,
  getConversations,
  sendDecision,
  sendMessage,
} from "@/api";
import { AppShell } from "@/components/shopper/AppShell";
import { DebugPanel } from "@/components/shopper/DebugPanel";
import { ProductCard } from "@/components/shopper/ProductCard";
import { timeAgo } from "@/lib/format";

export const Route = createFileRoute("/")({ component: ChatPage });

interface Turn {
  role: string; // user | assistant
  content: string;
}

function newConversationId(): string {
  return crypto.randomUUID();
}

function ChatPage() {
  const [conversationId, setConversationId] = useState(newConversationId);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [debug, setDebug] = useState<DebugTrace | null>(null);
  const [draft, setDraft] = useState("");
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [decision, setDecision] = useState<{ id: number; kind: "buy_now" | "watch" } | null>(null);
  const [past, setPast] = useState<ConversationSummary[]>([]);
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    void refreshPast();
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

  async function send() {
    const message = draft.trim();
    if (!message || searching) return;
    setDraft("");
    setError(null);
    setNotice(null);
    setTurns((current) => [...current, { role: "user", content: message }]);
    setSearching(true);
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

  // restoring shows the transcript only: the ranked products are not part of the stored
  // history, so the buy/watch buttons would have nothing valid to act on
  async function restore(id: string) {
    setError(null);
    try {
      const conversation = await getConversation(id);
      setConversationId(conversation.id);
      setTurns(conversation.history);
      setProducts([]);
      setDebug(null);
      setNotice("Reopened. Send a message to search again — earlier picks are not stored.");
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Request failed");
    }
  }

  return (
    <AppShell
      title="What are you hunting for?"
      subtitle="Describe it once. Shopper does the digging."
    >
      <div className="grid gap-6 lg:grid-cols-[1.1fr_1fr]">
        <section className="sticker rounded-3xl bg-card p-4">
          <details className="mb-3">
            <summary className="cursor-pointer text-sm font-extrabold">
              Past conversations ({past.length})
            </summary>
            <ul className="mt-2 space-y-1">
              {past.map((conversation) => (
                <li key={conversation.id}>
                  <button
                    onClick={() => void restore(conversation.id)}
                    className="w-full rounded-2xl bg-secondary/60 px-3 py-1.5 text-left text-sm font-semibold hover:bg-secondary"
                  >
                    {conversation.title || "(no message yet)"}
                    <span className="ml-2 text-xs text-muted-foreground">
                      {timeAgo(conversation.updated_at)}
                    </span>
                  </button>
                </li>
              ))}
              {past.length === 0 ? (
                <li className="text-sm text-muted-foreground">Nothing here yet.</li>
              ) : null}
            </ul>
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
                Say what you want, roughly. Shopper asks for anything it still needs.
              </p>
            ) : null}
            {searching ? (
              <div className="flex justify-start">
                <p className="sticker max-w-[85%] rounded-3xl rounded-bl-md bg-secondary px-4 py-2.5 text-sm font-semibold">
                  <span className="wobble inline-block">🍏</span> Searching Best Buy, Target and
                  Amazon, then reading the reviews. This takes 30-60 seconds.
                </p>
              </div>
            ) : null}
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
            <p className="sticker rounded-3xl bg-card p-4 text-sm font-semibold text-muted-foreground">
              Nothing ranked yet. Picks show up here once a search finishes.
            </p>
          ) : null}
        </section>
      </div>

      <DebugPanel trace={debug ?? undefined} />
    </AppShell>
  );
}
