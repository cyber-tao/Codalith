import { useCallback, useEffect, useRef, useState } from "react";

import { fetchDashboardSnapshot } from "../api";
import type { DashboardSnapshot, WindowKey } from "../types";

interface DashboardState {
  data: DashboardSnapshot | null;
  error: string | null;
  loading: boolean;
  refreshing: boolean;
  refresh: () => Promise<void>;
}

const REFRESH_INTERVAL_MS = 5_000;

export function useDashboard(
  range: WindowKey,
  target: string,
  live: boolean,
): DashboardState {
  const [data, setData] = useState<DashboardSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setRefreshing(true);
    try {
      const snapshot = await fetchDashboardSnapshot({
        range,
        target,
        signal: controller.signal,
      });
      setData(snapshot);
      setError(null);
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      setError(reason instanceof Error ? reason.message : "Unable to load dashboard");
    } finally {
      if (controllerRef.current === controller) {
        setRefreshing(false);
      }
    }
  }, [range, target]);

  useEffect(() => {
    void refresh();
    return () => controllerRef.current?.abort();
  }, [refresh]);

  useEffect(() => {
    if (!live) return undefined;
    const timer = window.setInterval(() => void refresh(), REFRESH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [live, refresh]);

  return {
    data,
    error,
    loading: data === null && error === null,
    refreshing,
    refresh,
  };
}
