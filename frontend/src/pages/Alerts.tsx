import { useEffect, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import MuiAlert from "@mui/material/Alert";
import Link from "@mui/material/Link";
import Stack from "@mui/material/Stack";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableContainer from "@mui/material/TableContainer";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import Typography from "@mui/material/Typography";
import DealBadge from "../components/DealBadge";
import { ApiError, getAlerts, safeUrl } from "../api";
import type { Alert } from "../api";

export default function Alerts() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getAlerts()
      .then(setAlerts)
      .catch((caught) => setError(caught instanceof ApiError ? caught.message : "Request failed"))
      .finally(() => setLoading(false));
  }, []);

  return (
    <Stack spacing={2}>
      <Typography variant="h6">Alerts</Typography>
      {error && <MuiAlert severity="error">{error}</MuiAlert>}
      {loading && <Typography>Loading...</Typography>}
      {!loading && alerts.length === 0 && <Typography>No alerts.</Typography>}
      {alerts.length > 0 && (
        // scrolls horizontally on a phone rather than squeezing the columns
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Item</TableCell>
                <TableCell>Reason</TableCell>
                <TableCell>Retailer</TableCell>
                <TableCell>Price</TableCell>
                <TableCell>Emailed</TableCell>
                <TableCell>Link</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {alerts.map((alert) => {
                const href = safeUrl(alert.url);
                return (
                  <TableRow key={alert.id}>
                    <TableCell>
                      {alert.item_id === null ? (
                        (alert.item_name ?? "-")
                      ) : (
                        <Link component={RouterLink} to={`/items/${alert.item_id}`}>
                          {alert.item_name ?? `item ${alert.item_id}`}
                        </Link>
                      )}
                    </TableCell>
                    <TableCell>
                      {alert.reason === null ? "-" : <DealBadge reason={alert.reason} />}
                    </TableCell>
                    <TableCell>{alert.retailer ?? "-"}</TableCell>
                    <TableCell>
                      {alert.price === null ? "-" : `$${alert.price.toFixed(2)}`}
                    </TableCell>
                    <TableCell>{alert.sent_at ?? "not sent"}</TableCell>
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
      )}
    </Stack>
  );
}
