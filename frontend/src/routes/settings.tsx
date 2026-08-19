import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";

import type { Profile } from "@/api";
import { ApiError, getProfile, updateLocation } from "@/api";
import { AppShell } from "@/components/shopper/AppShell";

export const Route = createFileRoute("/settings")({ component: SettingsPage });

// the key has to reach the browser for the Places JS API, so it is a VITE_ var.
// restricting it by HTTP referrer in the Google console is the user's job.
const PLACES_API_KEY = import.meta.env.VITE_GOOGLE_PLACES_API_KEY as string | undefined;

const GEOCODE_TIMEOUT_MS = 5000;

// only the few Google Maps JS members used here, so no @types/google.maps dependency
interface PlaceLike {
  location?: { lat(): number; lng(): number } | null;
  formattedAddress?: string | null;
  fetchFields(request: { fields: string[] }): Promise<unknown>;
}

interface SelectEvent extends Event {
  placePrediction?: { toPlace(): PlaceLike };
  place?: PlaceLike;
}

interface PlacesLibrary {
  PlaceAutocompleteElement?: new (options?: object) => HTMLElement;
}

interface GoogleMapsApi {
  maps: {
    importLibrary?: (name: string) => Promise<PlacesLibrary>;
    Geocoder: new () => {
      geocode(request: { location: { lat: number; lng: number } }): Promise<{
        results: { formatted_address?: string }[];
      }>;
    };
  };
}

declare global {
  interface Window {
    google?: GoogleMapsApi;
  }
}

// load the Maps JS bootstrap once; a second call reuses the tag already in the document.
// v=weekly + loading=async is what makes importLibrary available (the new Places API).
function loadMapsScript(): Promise<void> {
  if (window.google) return Promise.resolve();
  const existing = document.getElementById("google-maps-script") as HTMLScriptElement | null;
  const script = existing ?? document.createElement("script");
  const ready = new Promise<void>((resolve, reject) => {
    script.addEventListener("load", () => resolve());
    script.addEventListener("error", () => reject(new Error("Google Maps script failed to load")));
  });
  if (!existing) {
    script.id = "google-maps-script";
    script.async = true;
    script.src =
      `https://maps.googleapis.com/maps/api/js?key=${PLACES_API_KEY}` +
      `&libraries=places&v=weekly&loading=async`;
    document.head.appendChild(script);
  }
  return ready;
}

// with loading=async the script's load event fires before google.maps.importLibrary exists,
// so wait for it rather than reading the places namespace too early
async function placesLibrary(): Promise<PlacesLibrary> {
  for (let attempt = 0; attempt < 50; attempt++) {
    const importLibrary = window.google?.maps.importLibrary;
    if (importLibrary) return importLibrary("places");
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("google.maps.importLibrary never appeared");
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="text-xs font-extrabold uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <div className="mt-1.5">{children}</div>
    </label>
  );
}

function SettingsPage() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    getProfile()
      .then(setProfile)
      .catch((caught) => setError(caught instanceof ApiError ? caught.message : "Request failed"));
  }, []);

  useEffect(() => {
    if (!PLACES_API_KEY) return;
    let cancelled = false;
    loadMapsScript()
      .then(placesLibrary)
      .then((places) => {
        if (cancelled || !containerRef.current) return;
        // PlaceAutocompleteElement is the new Places API widget; the legacy
        // places.Autocomplete class is deliberately not used
        const ElementClass = places.PlaceAutocompleteElement;
        if (!ElementClass) {
          setError("Places API (New) is not enabled for this key.");
          return;
        }
        const element = new ElementClass();
        // the web component renders its own input; give it the row width
        element.style.width = "100%";
        containerRef.current.replaceChildren(element);
        // gmp-select is the current event; gmp-placeselect was the earlier name
        element.addEventListener("gmp-select", handleSelect as EventListener);
        element.addEventListener("gmp-placeselect", handleSelect as EventListener);
      })
      .catch(() =>
        setError("Google Places failed to load. Check the key and its API restrictions.")
      );
    return () => {
      cancelled = true;
    };
  }, []);

  // the event carries a prediction, not a place: the coordinates arrive only after fetchFields
  async function handleSelect(event: SelectEvent) {
    const place = event.placePrediction ? event.placePrediction.toPlace() : event.place;
    if (!place) return;
    try {
      await place.fetchFields({ fields: ["location", "formattedAddress"] });
    } catch {
      setError("Could not read that address.");
      return;
    }
    if (!place.location) {
      setError("That result has no coordinates. Pick another.");
      return;
    }
    await save(
      place.location.lat(),
      place.location.lng(),
      place.formattedAddress ?? "selected location"
    );
  }

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

  const located = profile !== null && profile.lat !== null && profile.lon !== null;

  return (
    <AppShell title="Settings" subtitle="Your location, used to find nearby stores.">
      <div className="grid gap-4 md:grid-cols-2">
        <section className="sticker space-y-4 rounded-3xl bg-card p-4">
          <h2 className="font-display text-xl font-extrabold">Location</h2>
          <Field label="Search an address">
            <div ref={containerRef} />
          </Field>
          {!PLACES_API_KEY ? (
            <p className="rounded-2xl bg-butter p-3 text-sm font-semibold">
              VITE_GOOGLE_PLACES_API_KEY is not set, so address autocomplete is off. The button
              below still works and saves raw coordinates.
            </p>
          ) : null}
          <button
            disabled={saving}
            onClick={useCurrentLocation}
            className="sticker rounded-full bg-sky px-4 py-2 text-sm font-extrabold transition-transform hover:-translate-y-0.5 disabled:opacity-50"
          >
            Use current location
          </button>
        </section>

        <section className="sticker space-y-3 rounded-3xl bg-card p-4">
          <h2 className="font-display text-xl font-extrabold">Saved</h2>
          <p className="text-sm font-semibold">
            {located
              ? `${profile.display_address ?? "unnamed"} (${profile.lat}, ${profile.lon})`
              : "No location set. Chat searches fail until one is saved."}
          </p>
          {/* radius is per watched item, not a profile setting, so there is no control for it here */}
          <p className="text-sm text-muted-foreground">
            Search radius is set per watched item, not here. The location is what Google Places
            uses to find the nearest Target, Best Buy and Micro Center.
          </p>
          {error ? (
            <p className="rounded-2xl bg-strawberry p-3 text-sm font-semibold text-accent-foreground">
              {error}
            </p>
          ) : null}
          {status ? (
            <p className="rounded-2xl bg-secondary p-3 text-sm font-semibold">{status}</p>
          ) : null}
        </section>
      </div>
    </AppShell>
  );
}
