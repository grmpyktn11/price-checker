import { useEffect, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Link from "@mui/material/Link";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import ProductCard from "../components/ProductCard";
import { ApiError, getProfile, safeUrl, sendDecision, sendMessage } from "../api";
import type { Decision, Product } from "../api";

type ChatEntry =
  | { kind: "user"; text: string }
  | { kind: "followup"; text: string }
  | { kind: "results"; narration: string; products: Product[] }
  | { kind: "system"; text: string; url?: string }
  | { kind: "error"; text: string };

export default function Chat() {
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [busyProductId, setBusyProductId] = useState<number | null>(null);
  // the message list is lost on reload anyway, so the id resets with it rather than resuming
  // a server-side history the user can no longer see
  const [conversationId, setConversationId] = useState(() => crypto.randomUUID());
  const [conversationExpired, setConversationExpired] = useState(false);
  const [locationMissing, setLocationMissing] = useState(false);

  useEffect(() => {
    // failure is swallowed: the 400 from /chat/message catches the same condition
    getProfile()
      .then((profile) => setLocationMissing(profile.lat === null || profile.lon === null))
      .catch(() => {});
  }, []);

  function appendEntry(entry: ChatEntry) {
    setEntries((current) => [...current, entry]);
  }

  function startNewConversation() {
    setConversationId(crypto.randomUUID());
    setEntries([]);
    setConversationExpired(false);
  }

  async function handleSend() {
    const text = input.trim();
    if (!text || sending) return;
    appendEntry({ kind: "user", text });
    setInput("");
    setSending(true);
    try {
      const response = await sendMessage(conversationId, text);
      if (response.type === "followup") {
        appendEntry({ kind: "followup", text: response.question });
      } else {
        appendEntry({
          kind: "results",
          narration: response.narration,
          products: response.products,
        });
      }
    } catch (error) {
      const apiError = error instanceof ApiError ? error : null;
      appendEntry({ kind: "error", text: apiError ? apiError.message : "Request failed" });
      if (apiError?.status === 400) setLocationMissing(true);
      // the backend discarded this turn in both cases, so restore the text for a clean retry
      if (apiError?.status === 400 || apiError?.status === 502) setInput(text);
    } finally {
      setSending(false);
    }
  }

  async function handleDecision(productId: number, decision: Decision) {
    setBusyProductId(productId);
    try {
      const response = await sendDecision(conversationId, productId, decision);
      if (response.decision === "watch") {
        appendEntry({ kind: "system", text: `${response.message} (item ${response.item_id})` });
      } else {
        appendEntry({ kind: "system", text: response.message, url: safeUrl(response.url) ?? undefined });
      }
    } catch (error) {
      const apiError = error instanceof ApiError ? error : null;
      appendEntry({ kind: "error", text: apiError ? apiError.message : "Request failed" });
      // every 404 here means the server no longer holds the results these cards came from
      if (apiError?.status === 404) setConversationExpired(true);
    } finally {
      setBusyProductId(null);
    }
  }

  return (
    <Stack spacing={2}>
      {locationMissing && (
        <Alert severity="warning">
          No location set. Chat searches will fail until you set one on{" "}
          <Link component={RouterLink} to="/settings">
            Settings
          </Link>
          .
        </Alert>
      )}

      {conversationExpired && (
        <Box>
          <Alert severity="warning">
            This conversation expired. Its results are gone from the server.
          </Alert>
          <Button onClick={startNewConversation}>Start new conversation</Button>
        </Box>
      )}

      <Stack spacing={2}>
        {entries.map((entry, index) => {
          if (entry.kind === "user") return <Typography key={index}>You: {entry.text}</Typography>;
          if (entry.kind === "followup")
            return <Typography key={index}>Assistant: {entry.text}</Typography>;
          if (entry.kind === "error")
            return (
              <Alert key={index} severity="error">
                {entry.text}
              </Alert>
            );
          if (entry.kind === "system")
            return (
              <Typography key={index}>
                {entry.text}{" "}
                {entry.url && (
                  <Link href={entry.url} target="_blank" rel="noreferrer">
                    {entry.url}
                  </Link>
                )}
              </Typography>
            );
          return (
            <Stack key={index} spacing={2}>
              <Typography>Assistant: {entry.narration}</Typography>
              {entry.products.length === 0 ? (
                <Typography>No products.</Typography>
              ) : (
                entry.products.map((product) => (
                  <ProductCard
                    key={product.product_id}
                    product={product}
                    onDecision={handleDecision}
                    busy={busyProductId === product.product_id}
                    disabled={conversationExpired}
                  />
                ))
              )}
            </Stack>
          );
        })}
      </Stack>

      {sending && <Typography>Sending...</Typography>}

      <Stack direction="row" spacing={1}>
        <TextField
          fullWidth
          size="small"
          value={input}
          disabled={sending}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") handleSend();
          }}
        />
        <Button variant="contained" disabled={sending || !input.trim()} onClick={handleSend}>
          Send
        </Button>
      </Stack>
    </Stack>
  );
}
