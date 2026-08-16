import Container from "@mui/material/Container";
import Typography from "@mui/material/Typography";
import Chat from "./pages/Chat";

// one page for now, so no router
export default function App() {
  return (
    <Container maxWidth="sm">
      <Typography variant="h5" gutterBottom>
        Deal Tracker
      </Typography>
      <Chat />
    </Container>
  );
}
