import { useCallback, useEffect, useState } from "react";

import { abortError, adminRequest, errorMessage } from "./api";

export function useResource<T>(
  path: string | null,
  token: string,
  intervalMs: number | ((data: T | null) => number) = 0,
) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);

  useEffect(() => {
    setData(null);
    setError(null);
  }, [path, token]);

  useEffect(() => {
    if (!path || !token) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;
    let latest: T | null = null;
    const load = async () => {
      controller = new AbortController();
      setPending(true);
      try {
        const next = await adminRequest<T>(path, token, { signal: controller.signal });
        latest = next;
        if (!stopped) {
          setData(next);
          setError(null);
        }
      } catch (reason) {
        if (!stopped && !abortError(reason)) setError(errorMessage(reason));
      } finally {
        if (!stopped) {
          setPending(false);
          const delay = typeof intervalMs === "function" ? intervalMs(latest) : intervalMs;
          if (delay > 0) timer = setTimeout(() => void load(), delay);
        }
      }
    };
    void load();
    return () => {
      stopped = true;
      controller?.abort();
      clearTimeout(timer);
    };
  }, [path, token, intervalMs, revision]);

  return { data, error, pending, refresh };
}
