import { BrowserRouter, Link as RouterLink, Route, Routes } from "react-router-dom";
import Container from "@mui/material/Container";
import Link from "@mui/material/Link";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Chat from "./pages/Chat";
import Watchlist from "./pages/Watchlist";
import ItemDetail from "./pages/ItemDetail";
import Alerts from "./pages/Alerts";
import Settings from "./pages/Settings";

const NAV = [
  { to: "/", label: "Chat" },
  { to: "/watchlist", label: "Watchlist" },
  { to: "/alerts", label: "Alerts" },
  { to: "/settings", label: "Settings" },
];

export default function App() {
  return (
    <BrowserRouter>
      <Container maxWidth="sm">
        <Typography variant="h5" gutterBottom>
          Deal Tracker
        </Typography>
        {/* flexWrap so the four links do not push the page wider than a phone screen */}
        <Stack direction="row" spacing={2} flexWrap="wrap" sx={{ mb: 2 }}>
          {NAV.map((entry) => (
            <Link key={entry.to} component={RouterLink} to={entry.to}>
              {entry.label}
            </Link>
          ))}
        </Stack>
        <Routes>
          <Route path="/" element={<Chat />} />
          <Route path="/watchlist" element={<Watchlist />} />
          <Route path="/items/:itemId" element={<ItemDetail />} />
          <Route path="/alerts" element={<Alerts />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </Container>
    </BrowserRouter>
  );
}
