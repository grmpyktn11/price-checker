import { useEffect, useRef, useState } from "react";
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { ApiError, getProfile, updateLocation } from "../api";
import type { Profile } from "../api";

// the key has to reach the browser for the Places JS API, so it is a VITE_ var.
// restricting it by HTTP referrer in the Google console is the user's job.
const PLACES_API_KEY = import.meta.env.VITE_GOOGLE_PLACES_API_KEY as string | undefined;

const GEOCODE_TIMEOUT_MS = 5000;

// only the few Google Maps JS members used here, so no @types/google.maps dependency
interface PlaceResult {
  formatted_address?: string;
  geometry?: { location: { lat(): number; lng(): number } };
}

interface AutocompleteInstance {
  addListener(event: string, handler: () => void): void;
  getPlace(): PlaceResult;
}

interface GoogleMapsApi {
  maps: {
    places: {
      Autocomplete: new (input: HTMLInputElement, options?: object) => AutocompleteInstance;
    };
    Geocoder: new () => {
      geocode(request: { location: { lat: number; lng: number } }): Promise<{
        results: PlaceResult[];
      }>;
    };
  };
}

declare global {
  interface Window {
    google?: GoogleMapsApi;
  }
}

// load the Places JS API once; a second call reuses the tag already in the document
function loadPlacesScript(): Promise<void> {
  if (window.google) return Promise.resolve();
  const existing = document.getElementById("google-places-script") as HTMLScriptElement | null;
  const script = existing ?? document.createElement("script");
  const ready = new Promise<void>((resolve, reject) => {
    script.addEventListener("load", () => resolve());
    script.addEventListener("error", () => reject(new Error("Google Places script failed to load")));
  });
  if (!existing) {
    script.id = "google-places-script";
    script.async = true;
    script.src = `https://maps.googleapis.com/maps/api/js?key=${PLACES_API_KEY}&libraries=places`;
    document.head.appendChild(script);
  }
  return ready;
}

export default function Settings() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    getProfile()
      .then(setProfile)
      .catch((caught) => setError(caught instanceof ApiError ? caught.message : "Request failed"));
  }, []);

  useEffect(() => {
    if (!PLACES_API_KEY) return;
    let cancelled = false;
    loadPlacesScript()
      .then(() => {
        if (cancelled || !inputRef.current || !window.google) return;
        const autocomplete = new window.google.maps.places.Autocomplete(inputRef.current, {
          types: ["geocode"],
        });
        autocomplete.addListener("place_changed", () => {
          const place = autocomplete.getPlace();
          if (!place.geometry) {
            setError("Pick an address from the dropdown.");
            return;
          }
          save(
            place.geometry.location.lat(),
            place.geometry.location.lng(),
            place.formatted_address ?? "selected location"
          );
        });
      })
      .catch(() => setError("Google Places script failed to load"));
    return () => {
      cancelled = true;
    };
  }, []);

  async function save(lat: number, lon: number, displayAddress: string) {
    setSaving(true);
    setStatus(null);
    try {
      const updated = await updateLocation(lat, lon, displayAddress);
      setProfile(updated);
      setStatus("Location saved.");
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Request failed");
    } finally {
      setSaving(false);
    }
  }

  // reverse geocode only to make the saved address readable; the backend needs the coords,
  // so a missing key, a rejected geocode, or one that never answers (the Geocoder hangs when
  // the Maps JS API is not enabled for the key) falls back to them instead of failing the save
  async function reverseGeocode(lat: number, lon: number): Promise<string> {
    const coordinates = `${lat.toFixed(4)}, ${lon.toFixed(4)}`;
    if (!PLACES_API_KEY || !window.google) return coordinates;
    const lookup = new window.google.maps.Geocoder()
      .geocode({ location: { lat, lng: lon } })
      .then((response) => response.results[0]?.formatted_address ?? coordinates)
      .catch(() => coordinates);
    const timeout = new Promise<string>((resolve) =>
      setTimeout(() => resolve(coordinates), GEOCODE_TIMEOUT_MS)
    );
    return Promise.race([lookup, timeout]);
  }

  function useCurrentLocation() {
    setStatus("Getting current location...");
    navigator.geolocation.getCurrentPosition(
      async (position) => {
        const { latitude, longitude } = position.coords;
        const address = await reverseGeocode(latitude, longitude);
        await save(latitude, longitude, address);
      },
      (positionError) => {
        setStatus(null);
        setError(`Could not get current location: ${positionError.message}`);
      }
    );
  }

  return (
    <Stack spacing={2}>
      <Typography variant="h6">Settings</Typography>

      {error && <Alert severity="error">{error}</Alert>}
      {status && <Alert severity="info">{status}</Alert>}
      {!PLACES_API_KEY && (
        <Alert severity="warning">
          VITE_GOOGLE_PLACES_API_KEY is not set, so address autocomplete is off. The current
          location button still works and saves raw coordinates.
        </Alert>
      )}

      <Typography>
        Current location:{" "}
        {profile && profile.lat !== null && profile.lon !== null
          ? `${profile.display_address ?? "unnamed"} (${profile.lat}, ${profile.lon})`
          : "not set"}
      </Typography>

      <TextField
        fullWidth
        size="small"
        label="Search an address"
        inputRef={inputRef}
        disabled={saving}
      />

      <Button variant="contained" disabled={saving} onClick={useCurrentLocation}>
        Use current location
      </Button>
    </Stack>
  );
}
