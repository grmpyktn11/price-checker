import { LineChart } from "@mui/x-charts/LineChart";
import Typography from "@mui/material/Typography";
import type { PricePoint } from "../api";

interface PriceHistoryChartProps {
  points: PricePoint[];
}

// backend timestamps are naive UTC, so append Z to stop the browser reading them as local
function toDate(recordedAt: string): Date {
  return new Date(recordedAt.endsWith("Z") ? recordedAt : `${recordedAt}Z`);
}

export default function PriceHistoryChart({ points }: PriceHistoryChartProps) {
  // a row with no price or no timestamp cannot be plotted
  const plottable = points.filter(
    (point): point is PricePoint & { price: number; recorded_at: string } =>
      point.price !== null && point.recorded_at !== null
  );
  if (plottable.length === 0) return <Typography>No price history.</Typography>;

  // one x value per recorded timestamp, shared by every series
  const times = [...new Set(plottable.map((point) => point.recorded_at))].sort();
  const listingIds = [...new Set(plottable.map((point) => point.listing_id))];

  // a listing has no point at another listing's timestamp, so those slots are null and
  // connectNulls draws through them
  const series = listingIds.map((listingId) => ({
    label: `listing ${listingId}`,
    connectNulls: true,
    data: times.map((time) => {
      const match = plottable.find(
        (point) => point.listing_id === listingId && point.recorded_at === time
      );
      return match ? match.price : null;
    }),
  }));

  return (
    <LineChart
      height={300}
      xAxis={[
        {
          data: times.map(toDate),
          scaleType: "time",
        },
      ]}
      series={series}
    />
  );
}
