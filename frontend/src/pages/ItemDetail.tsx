import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import Alert from "@mui/material/Alert";
import Link from "@mui/material/Link";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import ListingsTable from "../components/ListingsTable";
import PriceHistoryChart from "../components/PriceHistoryChart";
import { ApiError, getItem, getListings, getPriceHistory, getReviews, safeUrl } from "../api";
import type { Item, Listing, PricePoint, Review } from "../api";

export default function ItemDetail() {
  const { itemId } = useParams();
  const id = Number(itemId);
  const [item, setItem] = useState<Item | null>(null);
  const [listings, setListings] = useState<Listing[]>([]);
  const [points, setPoints] = useState<PricePoint[]>([]);
  const [reviews, setReviews] = useState<Review[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        const [loadedItem, loadedListings, loadedPoints, loadedReviews] = await Promise.all([
          getItem(id),
          getListings(id),
          getPriceHistory(id),
          getReviews(id),
        ]);
        setItem(loadedItem);
        setListings(loadedListings);
        setPoints(loadedPoints);
        setReviews(loadedReviews);
        setError(null);
      } catch (caught) {
        setError(caught instanceof ApiError ? caught.message : "Request failed");
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [id]);

  if (loading) return <Typography>Loading...</Typography>;

  return (
    <Stack spacing={2}>
      {error && <Alert severity="error">{error}</Alert>}

      <Typography variant="h6">{item ? (item.name ?? "(unnamed)") : `Item ${id}`}</Typography>
      {item && (
        <Typography>
          status {item.status ?? "unknown"}
          {item.category !== null && ` | category ${item.category}`}
          {item.budget_max !== null && ` | budget $${item.budget_max.toFixed(2)}`}
          {item.target_price !== null && ` | target $${item.target_price.toFixed(2)}`}
        </Typography>
      )}

      <Typography variant="subtitle1">Price history</Typography>
      <PriceHistoryChart points={points} />

      <Typography variant="subtitle1">Listings</Typography>
      <ListingsTable listings={listings} />

      <Typography variant="subtitle1">Reviews</Typography>
      {reviews.length === 0 && <Typography>No reviews.</Typography>}
      {reviews.map((review) => {
        const href = safeUrl(review.url);
        return (
          <Stack key={review.id} spacing={0.5}>
            <Typography>
              {/* sources ending in _inherited were attributed from another retailer's listing */}
              {review.source ?? "unknown source"}
              {review.rating !== null && ` | ${review.rating} stars`}
              {review.review_count !== null && ` | ${review.review_count} reviews`}
              {review.authenticity_flag !== null && ` | ${review.authenticity_flag}`}
            </Typography>
            {review.summary_text !== null && <Typography>{review.summary_text}</Typography>}
            {href && (
              <Link href={href} target="_blank" rel="noreferrer">
                {href}
              </Link>
            )}
          </Stack>
        );
      })}
    </Stack>
  );
}
