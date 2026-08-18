// Fetch a single incident by id (used by the incident detail view). This is a
// per-resource fetch, separate from the shared dashboard snapshot, so it has
// its own loading / error / not-found lifecycle.

import { useEffect, useState } from "react";

import { fetchIncident } from "../services/api";
import type { Incident } from "../types";

export interface IncidentState {
  incident: Incident | null;
  loading: boolean;
  error: string | null;
}

export function useIncident(id: string | undefined): IncidentState {
  const [incident, setIncident] = useState<Incident | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    setIncident(null);
    (async () => {
      try {
        const inc = await fetchIncident(String(id));
        if (active) {
          setIncident(inc);
          setLoading(false);
        }
      } catch (err) {
        if (active) {
          setError(err instanceof Error ? err.message : String(err));
          setLoading(false);
        }
      }
    })();
    return () => {
      active = false;
    };
  }, [id]);

  return { incident, loading, error };
}
