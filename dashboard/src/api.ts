import type { DashboardSnapshot, WindowKey } from "./types";

interface SnapshotOptions {
  range: WindowKey;
  target: string;
  signal?: AbortSignal;
}

export async function fetchDashboardSnapshot({
  range,
  target,
  signal,
}: SnapshotOptions): Promise<DashboardSnapshot> {
  const params = new URLSearchParams({ range });
  if (target) params.set("target", target);
  const response = await fetch(`/api/dashboard/snapshot?${params.toString()}`, {
    signal,
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as {
      error?: { message?: string };
    } | null;
    throw new Error(payload?.error?.message ?? `Dashboard request failed (${response.status})`);
  }
  return (await response.json()) as DashboardSnapshot;
}
