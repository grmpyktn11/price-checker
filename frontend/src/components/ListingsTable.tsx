import Link from "@mui/material/Link";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableContainer from "@mui/material/TableContainer";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import Typography from "@mui/material/Typography";
import { safeUrl } from "../api";
import type { Listing } from "../api";

interface ListingsTableProps {
  listings: Listing[];
}

// in_stock is boolean | null, so three cases
function stockText(inStock: boolean | null): string {
  if (inStock === null) return "unknown";
  return inStock ? "yes" : "no";
}

export default function ListingsTable({ listings }: ListingsTableProps) {
  if (listings.length === 0) return <Typography>No listings.</Typography>;
  return (
    // TableContainer scrolls horizontally on a phone instead of squeezing the columns
    <TableContainer>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Retailer</TableCell>
            <TableCell>Store</TableCell>
            <TableCell>Distance</TableCell>
            <TableCell>Price</TableCell>
            <TableCell>In stock</TableCell>
            <TableCell>Ships in</TableCell>
            <TableCell>Scraped</TableCell>
            <TableCell>Link</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {listings.map((listing) => {
            const href = safeUrl(listing.url);
            return (
              <TableRow key={listing.id}>
                <TableCell>{listing.retailer ?? "-"}</TableCell>
                <TableCell>{listing.store_name ?? listing.store_id ?? "online"}</TableCell>
                <TableCell>
                  {listing.distance_miles === null ? "-" : `${listing.distance_miles.toFixed(1)} mi`}
                </TableCell>
                <TableCell>
                  {listing.price === null ? "-" : `$${listing.price.toFixed(2)}`}
                </TableCell>
                <TableCell>{stockText(listing.in_stock)}</TableCell>
                <TableCell>
                  {listing.shipping_days_est === null ? "-" : `${listing.shipping_days_est} d`}
                </TableCell>
                <TableCell>{listing.scraped_at ?? "-"}</TableCell>
                <TableCell>
                  {href ? (
                    <Link href={href} target="_blank" rel="noreferrer">
                      open
                    </Link>
                  ) : (
                    "-"
                  )}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </TableContainer>
  );
}
