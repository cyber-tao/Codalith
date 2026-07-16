export type WindowKey = "15m" | "1h" | "6h" | "24h" | "7d";
export type HealthSeverity = "warning" | "error";
export type LogLevel = "INFO" | "WARN" | "ERROR";

export interface DashboardWindow {
  key: WindowKey;
  label: string;
  seconds: number;
  bucket_seconds: number;
}

export interface ServiceStatus {
  version: string;
  started_at: string;
  uptime_seconds: number;
  ready: boolean;
  target: string;
}

export interface DashboardTarget {
  id: string;
  label: string;
  kind: "corpus" | "workspace";
}

export interface Summary {
  total_queries: number;
  p50_latency_ms: number;
  p95_latency_ms: number;
  success_rate: number;
  peak_memory_mb: number;
  error_count: number;
  degraded_count: number;
}

export interface Comparison {
  total_queries_percent: number | null;
  p95_latency_percent: number | null;
  success_rate_points: number | null;
  peak_memory_percent: number | null;
}

export interface SeriesPoint {
  timestamp: string;
  calls: number;
  calls_per_minute: number;
  errors: number;
  p50_latency_ms: number | null;
  p95_latency_ms: number | null;
  cpu_percent: number | null;
  peak_memory_mb: number | null;
}

export interface ToolUsageRow {
  name: string;
  calls: number;
  share: number;
  p95_latency_ms: number;
  error_rate: number;
}

export interface CorpusStatus {
  corpus_id: string;
  revision: string;
  state: "ready" | "degraded" | "missing" | "invalid";
  generation_id: string | null;
  semantic_available: boolean;
  files: number;
  symbols: number;
  references: number;
  module_dependencies: number;
  message?: string | null;
}

export interface RecentQuery {
  id: string;
  timestamp: string;
  tool: string;
  target: string;
  query: string;
  duration_ms: number;
  status: "success" | "error";
  degraded: boolean;
  result_count: number;
  error_code: string | null;
}

export interface LogRow {
  id: string;
  timestamp: string;
  level: LogLevel;
  source: string;
  message: string;
  target: string;
  duration_ms: number;
  details: Record<string, unknown>;
}

export interface Alert {
  id: string;
  severity: HealthSeverity;
  title: string;
  message: string;
}

export interface Retention {
  events_stored: number;
  event_capacity: number;
  logs_stored: number;
  log_capacity: number;
}

export interface DashboardSnapshot {
  generated_at: string;
  window: DashboardWindow;
  service: ServiceStatus;
  targets: DashboardTarget[];
  summary: Summary;
  comparison: Comparison;
  series: SeriesPoint[];
  tools: ToolUsageRow[];
  corpora: CorpusStatus[];
  recent_queries: RecentQuery[];
  logs: LogRow[];
  alerts: Alert[];
  retention: Retention;
}
