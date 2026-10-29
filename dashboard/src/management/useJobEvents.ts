import { useEffect, useRef, useState } from "react";

import { abortError, adminRequest, errorMessage } from "./api";
import type { ItemList, JobEvent } from "./types";

const MAX_VISIBLE_EVENTS = 500;

export function useJobEvents(jobId: string, token: string, intervalMs: number) {
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const pollingInterval = useRef(intervalMs);

  useEffect(() => { pollingInterval.current = intervalMs; }, [intervalMs]);

  useEffect(() => {
    setEvents([]);
    setError(null);
    if (!jobId || !token) return;
    let stopped = false;
    let afterSeq: number | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;
    const load = async () => {
      controller = new AbortController();
      let catchUp = false;
      try {
        const initial = afterSeq === null;
        const result = await adminRequest<ItemList<JobEvent>>(
          `jobs/${encodeURIComponent(jobId)}/events?${initial ? `tail=${MAX_VISIBLE_EVENTS}` : `after_seq=${afterSeq}`}`,
          token,
          { signal: controller.signal },
        );
        if (!stopped) {
          const cursor = afterSeq ?? 0;
          const additions = result.items.filter((event) => event.seq > cursor);
          afterSeq = cursor;
          if (additions.length) {
            afterSeq = Math.max(afterSeq, ...additions.map((event) => event.seq));
            setEvents((previous) => [...previous, ...additions].slice(-MAX_VISIBLE_EVENTS));
          }
          catchUp = !initial && result.items.length === MAX_VISIBLE_EVENTS;
          setError(null);
        }
      } catch (reason) {
        if (!stopped && !abortError(reason)) setError(errorMessage(reason));
      } finally {
        if (!stopped) timer = setTimeout(() => void load(), catchUp ? 0 : pollingInterval.current);
      }
    };
    void load();
    return () => {
      stopped = true;
      controller?.abort();
      clearTimeout(timer);
    };
  }, [jobId, token]);

  return { events, error };
}
