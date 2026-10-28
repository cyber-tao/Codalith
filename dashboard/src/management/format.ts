import type { JobStatus } from "./types";

export const ACTIVE_STATUSES = new Set<JobStatus>([
  "queued", "running", "validating", "publishing", "cancel_requested",
]);

export const RESUMABLE_STATUSES = new Set<JobStatus>(["interrupted", "cancelled"]);
export const RETRYABLE_STATUSES = new Set<JobStatus>(["failed", "partial", "partial_success"]);

export function readable(value: string): string {
  return value.replaceAll("_", " ");
}

export function bytes(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "Not measured";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let amount = value;
  let index = 0;
  while (amount >= 1024 && index < units.length - 1) {
    amount /= 1024;
    index += 1;
  }
  return `${amount.toLocaleString("en", { maximumFractionDigits: index === 0 ? 0 : 2 })} ${units[index]}`;
}

export function jobError(value: unknown): string {
  if (!value) return "";
  if (typeof value === "string") return value;
  if (typeof value === "object" && "message" in value) return String(value.message);
  return JSON.stringify(value);
}

export function phasePercent(completed: number, total: number | null, succeeded: boolean): number | null {
  if (succeeded) return 100;
  if (!total || total < 0) return null;
  return Math.min(100, Math.max(0, completed / total * 100));
}
