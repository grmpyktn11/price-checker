import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardActions from "@mui/material/CardActions";
import CardContent from "@mui/material/CardContent";
import Link from "@mui/material/Link";
import Typography from "@mui/material/Typography";
import { safeUrl } from "../api";
import type { Decision, Product } from "../api";

interface ProductCardProps {
  product: Product;
  onDecision: (productId: number, decision: Decision) => void;
  busy: boolean; // a decision for this card is in flight
  disabled: boolean; // conversation expired
}

// in_stock is boolean | null, so three cases
function stockText(inStock: boolean | null): string {
  if (inStock === null) return "stock unknown";
  return inStock ? "in stock" : "out of stock";
}

export default function ProductCard({ product, onDecision, busy, disabled }: ProductCardProps) {
  const name = product.name ?? "(unnamed)";
  const href = safeUrl(product.url);
  return (
    <Card>
      <CardContent>
        <Typography>
          {href ? (
            <Link href={href} target="_blank" rel="noreferrer">
              {name}
            </Link>
          ) : (
            name
          )}
        </Typography>
        <Typography>
          {product.price === null ? "price unavailable" : `$${product.price.toFixed(2)}`}
        </Typography>
        <Typography>{product.retailer}</Typography>
        <Typography>{stockText(product.in_stock)}</Typography>
        {product.rating !== null && (
          <Typography>
            {product.rating} ({product.review_count ?? 0} reviews)
          </Typography>
        )}
        {product.store_id !== null && <Typography>store {product.store_id}</Typography>}
        {product.distance_miles !== null && (
          <Typography>{product.distance_miles.toFixed(1)} mi</Typography>
        )}
        {/* sub-scores are shown because this is a harness for validating the ranking */}
        <Typography>
          score {product.final_score.toFixed(2)} | spec {product.spec_match.toFixed(2)} review{" "}
          {product.review_score.toFixed(2)} price {product.price_score.toFixed(2)} distance{" "}
          {product.distance_score.toFixed(2)} nice {product.nice_to_have_score.toFixed(2)}
        </Typography>
      </CardContent>
      <CardActions>
        <Button
          disabled={busy || disabled}
          onClick={() => onDecision(product.product_id, "buy_now")}
        >
          Buy now
        </Button>
        <Button disabled={busy || disabled} onClick={() => onDecision(product.product_id, "watch")}>
          Watch
        </Button>
      </CardActions>
    </Card>
  );
}
