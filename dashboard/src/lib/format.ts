import type { LogLevel, LogRow } from "../types";

export function compactNumber(value: number): string {
  return new Intl.NumberFormat("en", {
    notation: value >= 1_000 ? "compact" : "standard",
    maximumFractionDigits: value >= 1_000 ? 1 : 0,
  }).format(value);
}

export function formatDuration(value: number): string {
  if (value < 1_000) return `${Math.round(value)} ms`;
  return `${(value / 1_000).toFixed(value >= 10_000 ? 1 : 2)} s`;
}

export function formatMemory(megabytes: number): string {
  if (megabytes >= 1_024) return `${(megabytes / 1_024).toFixed(2)} GB`;
  return `${Math.round(megabytes)} MB`;
}

export function formatPercent(value: number, digits = 1): string {
  return `${value.toFixed(digits)}%`;
}

export function formatTime(value: string, includeSeconds = false): string {
  return new Intl.DateTimeFormat("en", {
    hour: "2-digit",
    minute: "2-digit",
    second: includeSeconds ? "2-digit" : undefined,
    hour12: false,
  }).format(new Date(value));
}

export function formatUptime(seconds: number): string {
  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

export function toolLabel(value: string): string {
  return value.replace(/^codalith_/, "").replaceAll("_", " ");
}

export function filterLogs(
  logs: LogRow[],
  query: string,
  level: LogLevel | "ALL",
  source: string,
): LogRow[] {
  const normalized = query.trim().toLocaleLowerCase();
  return logs.filter((log) => {
    if (level !== "ALL" && log.level !== level) return false;
    if (source && log.source !== source) return false;
    if (!normalized) return true;
    return `${log.message} ${log.source} ${log.target} ${JSON.stringify(log.details)}`
      .toLocaleLowerCase()
      .includes(normalized);
  });
}
