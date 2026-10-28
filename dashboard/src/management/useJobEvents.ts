import { useEffect, useState } from "react";

import { abortError, adminRequest, errorMessage } from "./api";
import type { ItemList, JobEvent } from "./types";

const MAX_VISIBLE_EVENTS = 500;

export function useJobEvents(jobId: string, token: string, intervalMs: number) {
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setEvents([]);
    setError(null);
    if (!jobId || !token) return;
    let stopped = false;
    let afterSeq = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;
    const load = async () => {
      controller = new AbortController();
      try {
        const result = await adminRequest<ItemList<JobEvent>>(
          `jobs/${encodeURIComponent(jobId)}/events?after_seq=${afterSeq}`,
          token,
          { signal: controller.signal },
        );
        if (!stopped) {
          const additions = result.items.filter((event) => event.seq > afterSeq);
          if (additions.length) {
            afterSeq = Math.max(afterSeq, ...additions.map((event) => event.seq));
            setEvents((previous) => [...previous, ...additions].slice(-MAX_VISIBLE_EVENTS));
          }
          setError(null);
        }
      } catch (reason) {
        if (!stopped && !abortError(reason)) setError(errorMessage(reason));
      } finally {
        if (!stopped) timer = setTimeout(() => void load(), intervalMs);
      }
    };
    void load();
    return () => {
      stopped = true;
      controller?.abort();
      clearTimeout(timer);
    };
  }, [jobId, token, intervalMs]);

  return { events, error };
}
