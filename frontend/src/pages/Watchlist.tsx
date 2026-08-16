import { useEffect, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardActions from "@mui/material/CardActions";
import CardContent from "@mui/material/CardContent";
import Link from "@mui/material/Link";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import DealBadge from "../components/DealBadge";
import { ApiError, createItem, deleteItem, getAlerts, getItems, getListings, rescanItem } from "../api";
import type { Item, Listing } from "../api";

// best option = cheapest in-stock listing, falling back to the cheapest priced one
function bestListing(listings: Listing[]): Listing | null {
  const priced = listings.filter((listing) => listing.price !== null);
  const inStock = priced.filter((listing) => listing.in_stock === true);
  const pool = inStock.length > 0 ? inStock : priced;
  if (pool.length === 0) return null;
  return pool.reduce((best, listing) => (listing.price! < best.price! ? listing : best));
}

export default function Watchlist() {
  const [items, setItems] = useState<Item[]>([]);
  const [bestByItem, setBestByItem] = useState<Record<number, Listing | null>>({});
  const [reasonsByItem, setReasonsByItem] = useState<Record<number, string[]>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [busyItemId, setBusyItemId] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const loadedItems = await getItems();
      setItems(loadedItems);
      // one listings call per item: there is no summary endpoint and the list is short
      const listingLists = await Promise.all(loadedItems.map((item) => getListings(item.id)));
      const best: Record<number, Listing | null> = {};
      loadedItems.forEach((item, index) => {
        best[item.id] = bestListing(listingLists[index]);
      });
      setBestByItem(best);
      // deal badges come from the alerts already recorded for each item
      const alerts = await getAlerts();
      const reasons: Record<number, string[]> = {};
      for (const alert of alerts) {
        if (alert.item_id === null || alert.reason === null) continue;
        const seen = reasons[alert.item_id] ?? [];
        if (!seen.includes(alert.reason)) reasons[alert.item_id] = [...seen, alert.reason];
      }
      setReasonsByItem(reasons);
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Request failed");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function handleAdd() {
    const name = newName.trim();
    if (!name || adding) return;
    setAdding(true);
    try {
      await createItem({ name });
      setNewName("");
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Request failed");
    } finally {
      setAdding(false);
    }
  }

  async function handleRemove(itemId: number) {
    setBusyItemId(itemId);
    try {
      await deleteItem(itemId);
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Request failed");
    } finally {
      setBusyItemId(null);
    }
  }

  // a rescan runs the real pipeline, so it can take a while before the listings change
  async function handleRescan(itemId: number) {
    setBusyItemId(itemId);
    try {
      await rescanItem(itemId);
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Request failed");
    } finally {
      setBusyItemId(null);
    }
  }

  return (
    <Stack spacing={2}>
      <Typography variant="h6">Watchlist</Typography>

      {error && <Alert severity="error">{error}</Alert>}

      <Stack direction="row" spacing={1}>
        <TextField
          fullWidth
          size="small"
          label="Item name"
          value={newName}
          onChange={(event) => setNewName(event.target.value)}
        />
        <Button variant="contained" disabled={adding || !newName.trim()} onClick={handleAdd}>
          Add
        </Button>
      </Stack>

      {loading && <Typography>Loading...</Typography>}
      {!loading && items.length === 0 && <Typography>No items watched.</Typography>}

      {items.map((item) => {
        const best = bestByItem[item.id] ?? null;
        const reasons = reasonsByItem[item.id] ?? [];
        return (
          <Card key={item.id}>
            <CardContent>
              <Typography>
                <Link component={RouterLink} to={`/items/${item.id}`}>
                  {item.name ?? "(unnamed)"}
                </Link>
              </Typography>
              <Typography>status {item.status ?? "unknown"}</Typography>
              {item.target_price !== null && (
                <Typography>target ${item.target_price.toFixed(2)}</Typography>
              )}
              <Typography>
                {best === null
                  ? "best option: none yet"
                  : `best option: $${best.price!.toFixed(2)} at ${best.retailer ?? "unknown"}` +
                    ` (${best.in_stock === true ? "in stock" : best.in_stock === false ? "out of stock" : "stock unknown"})`}
              </Typography>
              <Stack direction="row" spacing={1}>
                {reasons.map((reason) => (
                  <DealBadge key={reason} reason={reason} />
                ))}
              </Stack>
            </CardContent>
            <CardActions>
              <Button disabled={busyItemId === item.id} onClick={() => handleRescan(item.id)}>
                Rescan
              </Button>
              <Button disabled={busyItemId === item.id} onClick={() => handleRemove(item.id)}>
                Remove
              </Button>
            </CardActions>
          </Card>
        );
      })}
    </Stack>
  );
}
