import Chip from "@mui/material/Chip";

// alerts.reason values, spelled out for display
const REASON_LABELS: Record<string, string> = {
  price_drop: "price drop",
  target_hit: "target hit",
  new_alternative: "new alternative",
};

interface DealBadgeProps {
  reason: string;
}

export default function DealBadge({ reason }: DealBadgeProps) {
  return <Chip size="small" label={REASON_LABELS[reason] ?? reason} />;
}
