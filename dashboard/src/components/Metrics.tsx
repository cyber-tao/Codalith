import type { ReactNode } from "react";

import { Icon, type IconName } from "./Icons";
import {
  compactNumber,
  formatDuration,
  formatMemory,
  formatPercent,
} from "../lib/format";
import type { Comparison, DashboardSnapshot, SeriesPoint } from "../types";

interface StatusBannerProps {
  snapshot: DashboardSnapshot;
  onInspect: () => void;
}

export function StatusBanner({ snapshot, onInspect }: StatusBannerProps) {
  const healthy = snapshot.service.ready && snapshot.alerts.length === 0;
  return (
    <button className={healthy ? "status-banner healthy" : "status-banner attention"} onClick={onInspect} type="button">
      <span className="status-emblem">
        <Icon name={healthy ? "check" : "alert"} size={22} />
      </span>
      <span>
        <strong>{healthy ? "All systems operational" : "Attention required"}</strong>
        <small>
          {healthy
            ? `${snapshot.corpora.length} corpora ready · no active incidents`
            : `${snapshot.alerts.length} active incident${snapshot.alerts.length === 1 ? "" : "s"}`}
        </small>
      </span>
      <span className="banner-updated">{snapshot.window.label}</span>
      <Icon name="chevron" />
    </button>
  );
}

interface MetricDefinition {
  label: string;
  value: ReactNode;
  icon: IconName;
  comparison: number | null;
  comparisonLabel: string;
  inverse?: boolean;
  series: number[];
}

export function MetricStrip({ snapshot }: { snapshot: DashboardSnapshot }) {
  const { summary, comparison } = snapshot;
  const definitions: MetricDefinition[] = [
    {
      label: "Total queries",
      value: compactNumber(summary.total_queries),
      icon: "search",
      comparison: comparison.total_queries_percent,
      comparisonLabel: "vs prior window",
      series: snapshot.series.map((item) => item.calls),
    },
    {
      label: "P95 latency",
      value: formatDuration(summary.p95_latency_ms),
      icon: "clock",
      comparison: comparison.p95_latency_percent,
      comparisonLabel: "vs prior window",
      inverse: true,
      series: defined(snapshot.series, "p95_latency_ms"),
    },
    {
      label: "Success rate",
      value: formatPercent(summary.success_rate, 2),
      icon: "check",
      comparison: comparison.success_rate_points,
      comparisonLabel: "points vs prior",
      series: snapshot.series.map((item) => (item.calls ? ((item.calls - item.errors) / item.calls) * 100 : 100)),
    },
    {
      label: "Peak memory",
      value: formatMemory(summary.peak_memory_mb),
      icon: "memory",
      comparison: comparison.peak_memory_percent,
      comparisonLabel: "vs prior window",
      inverse: true,
      series: defined(snapshot.series, "peak_memory_mb"),
    },
  ];
  return (
    <section className="metric-strip" aria-label="Service summary">
      {definitions.map((metric) => (
        <MetricCard key={metric.label} {...metric} />
      ))}
    </section>
  );
}

function MetricCard({ label, value, icon, comparison, comparisonLabel, inverse, series }: MetricDefinition) {
  const improving = comparison === null ? null : inverse ? comparison <= 0 : comparison >= 0;
  return (
    <article className="metric-card">
      <div className="metric-heading">
        <span>{label}</span>
        <Icon name={icon} size={16} />
      </div>
      <div className="metric-content">
        <strong>{value}</strong>
        <Sparkline values={series} />
      </div>
      <div className={improving === null ? "metric-change neutral" : improving ? "metric-change good" : "metric-change warn"}>
        {comparison === null ? "No prior baseline" : `${comparison > 0 ? "+" : ""}${comparison.toFixed(2)}${label === "Success rate" ? " pp" : "%"}`}
        <span>{comparisonLabel}</span>
      </div>
    </article>
  );
}

function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2 || Math.max(...values) === Math.min(...values)) {
    return <span className="sparkline-empty" aria-hidden="true" />;
  }
  const width = 110;
  const height = 34;
  const max = Math.max(...values);
  const min = Math.min(...values);
  const points = values
    .map((value, index) => {
      const x = (index / Math.max(values.length - 1, 1)) * width;
      const y = height - ((value - min) / Math.max(max - min, 1)) * (height - 4) - 2;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg className="sparkline" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Metric trend">
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth="2" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function defined(points: SeriesPoint[], key: "p95_latency_ms" | "peak_memory_mb"): number[] {
  return points.flatMap((item) => (item[key] === null ? [] : [item[key]]));
}
