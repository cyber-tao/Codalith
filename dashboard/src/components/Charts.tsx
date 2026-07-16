import { useMemo, useState, type ReactNode } from "react";

import { Icon } from "./Icons";
import {
  compactNumber,
  formatDuration,
  formatMemory,
  formatPercent,
  formatTime,
  toolLabel,
} from "../lib/format";
import type { CorpusStatus, SeriesPoint, ToolUsageRow } from "../types";

interface PanelProps {
  id?: string;
  title: string;
  subtitle: string;
  action?: ReactNode;
  className?: string;
  children: ReactNode;
}

export function Panel({ id, title, subtitle, action, className = "", children }: PanelProps) {
  return (
    <section id={id} className={`panel ${className}`.trim()}>
      <header className="panel-header">
        <div>
          <h2>{title}</h2>
          <p>{subtitle}</p>
        </div>
        {action}
      </header>
      {children}
    </section>
  );
}

type QueryMetric = "traffic" | "latency" | "both";

export function QueryTrafficChart({ series }: { series: SeriesPoint[] }) {
  const [metric, setMetric] = useState<QueryMetric>("both");
  const width = 760;
  const height = 250;
  const plot = { left: 48, right: 52, top: 18, bottom: 42 };
  const plotWidth = width - plot.left - plot.right;
  const plotHeight = height - plot.top - plot.bottom;
  const maxCalls = Math.max(1, ...series.map((item) => item.calls_per_minute));
  const maxLatency = Math.max(1, ...series.map((item) => item.p95_latency_ms ?? 0));
  const x = (index: number) => plot.left + (index / Math.max(series.length - 1, 1)) * plotWidth;
  const callY = (value: number) => plot.top + plotHeight - (value / maxCalls) * plotHeight;
  const latencyY = (value: number) => plot.top + plotHeight - (value / maxLatency) * plotHeight;
  const latencyPath = linePath(series, x, (item) => item.p95_latency_ms, latencyY);
  const ticks = tickIndexes(series.length, 5);

  return (
    <Panel
      id="queries"
      title="Query traffic & latency"
      subtitle="Calls per minute and observed P95 tool latency"
      className="query-panel"
      action={
        <div className="segmented compact" aria-label="Chart metric">
          {(["traffic", "latency", "both"] as QueryMetric[]).map((item) => (
            <button key={item} type="button" className={metric === item ? "active" : ""} onClick={() => setMetric(item)}>
              {item[0].toUpperCase() + item.slice(1)}
            </button>
          ))}
        </div>
      }
    >
      {series.some((item) => item.calls > 0) ? (
        <div className="chart-frame">
          <svg className="query-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby="query-chart-title query-chart-desc">
            <title id="query-chart-title">Query traffic and latency over time</title>
            <desc id="query-chart-desc">Cyan bars show calls per minute. Amber line shows P95 latency in milliseconds.</desc>
            {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
              const y = plot.top + plotHeight * ratio;
              return <line key={ratio} x1={plot.left} x2={width - plot.right} y1={y} y2={y} className="grid-line" />;
            })}
            {metric !== "latency"
              ? series.map((item, index) => {
                  const slot = plotWidth / Math.max(series.length, 1);
                  const barWidth = Math.max(2, Math.min(8, slot * 0.58));
                  const y = callY(item.calls_per_minute);
                  return (
                    <rect
                      key={item.timestamp}
                      x={x(index) - barWidth / 2}
                      y={y}
                      width={barWidth}
                      height={Math.max(0, plot.top + plotHeight - y)}
                      rx="1"
                      className={item.errors ? "traffic-bar has-error" : "traffic-bar"}
                    >
                      <title>{`${formatTime(item.timestamp, true)} · ${item.calls_per_minute.toFixed(1)} calls/min${item.errors ? ` · ${item.errors} errors` : ""}`}</title>
                    </rect>
                  );
                })
              : null}
            {metric !== "traffic" && latencyPath ? <path d={latencyPath} className="latency-line" /> : null}
            {ticks.map((index) => (
              <text key={series[index]?.timestamp} x={x(index)} y={height - 12} textAnchor="middle" className="axis-label">
                {series[index] ? formatTime(series[index].timestamp) : ""}
              </text>
            ))}
            <text x="4" y={plot.top + 4} className="axis-label strong">
              {compactNumber(maxCalls)}/min
            </text>
            <text x={width - 3} y={plot.top + 4} textAnchor="end" className="axis-label strong">
              {formatDuration(maxLatency)}
            </text>
          </svg>
          <div className="chart-legend">
            <span><i className="legend-bar" /> Calls/min</span>
            <span><i className="legend-line latency" /> P95 latency</span>
            <span><i className="legend-dot error" /> Error bucket</span>
          </div>
        </div>
      ) : (
        <ChartEmpty message="Run an MCP query to start the traffic timeline." />
      )}
    </Panel>
  );
}

export function ToolUsage({ tools, onSelect }: { tools: ToolUsageRow[]; onSelect: (tool: string) => void }) {
  const maxCalls = Math.max(1, ...tools.map((item) => item.calls));
  return (
    <Panel title="Tool usage" subtitle="Ranked by calls in the selected window" className="tool-panel">
      {tools.length ? (
        <div className="ranked-bars">
          {tools.slice(0, 7).map((tool, index) => (
            <button className="ranked-row" type="button" key={tool.name} onClick={() => onSelect(tool.name)}>
              <span className="rank">{index + 1}</span>
              <span className="tool-name">{toolLabel(tool.name)}</span>
              <span className="bar-track" aria-hidden="true">
                <span style={{ width: `${Math.max(3, (tool.calls / maxCalls) * 100)}%` }} />
              </span>
              <strong>{compactNumber(tool.calls)}</strong>
              <span>{formatPercent(tool.share)}</span>
              <small>P95 {formatDuration(tool.p95_latency_ms)}</small>
            </button>
          ))}
        </div>
      ) : (
        <ChartEmpty message="Tool distribution appears after the first MCP call." />
      )}
    </Panel>
  );
}

export function ResourcePressure({ series }: { series: SeriesPoint[] }) {
  const points = series.filter((item) => item.cpu_percent !== null || item.peak_memory_mb !== null);
  const width = 420;
  const height = 188;
  const plot = { left: 40, right: 48, top: 16, bottom: 32 };
  const plotWidth = width - plot.left - plot.right;
  const plotHeight = height - plot.top - plot.bottom;
  const memoryMax = Math.max(1, ...points.map((item) => item.peak_memory_mb ?? 0));
  const x = (index: number) => plot.left + (index / Math.max(points.length - 1, 1)) * plotWidth;
  const cpuY = (value: number) => plot.top + plotHeight - (value / 100) * plotHeight;
  const memoryY = (value: number) => plot.top + plotHeight - (value / memoryMax) * plotHeight;
  const cpuPath = linePath(points, x, (item) => item.cpu_percent, cpuY);
  const memoryPath = linePath(points, x, (item) => item.peak_memory_mb, memoryY);
  const latest = points.at(-1);
  return (
    <Panel
      id="performance"
      title="Resource pressure"
      subtitle="Process CPU and peak resident memory"
      className="resource-panel"
      action={<span className="fresh-tag"><span className="status-dot live" /> sampled live</span>}
    >
      <div className="resource-summary">
        <span><i className="legend-line cpu" /> CPU <strong>{formatPercent(latest?.cpu_percent ?? 0)}</strong></span>
        <span><i className="legend-line memory" /> Peak RSS <strong>{formatMemory(latest?.peak_memory_mb ?? 0)}</strong></span>
      </div>
      <svg className="resource-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby="resource-title resource-desc">
        <title id="resource-title">CPU and peak memory over time</title>
        <desc id="resource-desc">Cyan line is process CPU percent. Amber line is peak resident memory.</desc>
        {[0, 0.5, 1].map((ratio) => {
          const y = plot.top + plotHeight * ratio;
          return <line key={ratio} x1={plot.left} x2={width - plot.right} y1={y} y2={y} className="grid-line" />;
        })}
        <path d={cpuPath} className="resource-line cpu" />
        <path d={memoryPath} className="resource-line memory" />
        <text x="5" y={plot.top + 4} className="axis-label strong">100%</text>
        <text x={width - 2} y={plot.top + 4} textAnchor="end" className="axis-label strong">{formatMemory(memoryMax)}</text>
        {points.length ? (
          <>
            <text x={plot.left} y={height - 8} className="axis-label">{formatTime(points[0].timestamp)}</text>
            <text x={width - plot.right} y={height - 8} textAnchor="end" className="axis-label">{formatTime(points.at(-1)?.timestamp ?? points[0].timestamp)}</text>
          </>
        ) : null}
      </svg>
    </Panel>
  );
}

export function CorpusFootprint({ corpora }: { corpora: CorpusStatus[] }) {
  const ready = corpora.filter((corpus) => corpus.state === "ready").length;
  const total = Math.max(corpora.length, 1);
  const radius = 42;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - ready / total);
  const totals = useMemo(
    () =>
      corpora.reduce(
        (result, item) => ({
          files: result.files + item.files,
          symbols: result.symbols + item.symbols,
          references: result.references + item.references,
        }),
        { files: 0, symbols: 0, references: 0 },
      ),
    [corpora],
  );
  return (
    <Panel id="corpora" title="Corpus footprint" subtitle="Published generation size and semantic readiness" className="corpus-panel">
      <div className="corpus-overview">
        <div className="readiness-ring">
          <svg viewBox="0 0 100 100" role="img" aria-label={`${ready} of ${corpora.length} corpora ready`}>
            <circle cx="50" cy="50" r={radius} className="ring-track" />
            <circle
              cx="50"
              cy="50"
              r={radius}
              className="ring-value"
              strokeDasharray={circumference}
              strokeDashoffset={offset}
            />
          </svg>
          <span><strong>{ready}/{corpora.length}</strong>ready</span>
        </div>
        <div className="corpus-totals">
          <span><strong>{compactNumber(totals.files)}</strong>files</span>
          <span><strong>{compactNumber(totals.symbols)}</strong>symbols</span>
          <span><strong>{compactNumber(totals.references)}</strong>references</span>
        </div>
      </div>
      <div className="corpus-list">
        {corpora.map((corpus) => (
          <article key={corpus.corpus_id}>
            <div>
              <strong>{corpus.corpus_id}</strong>
              <span>{corpus.revision}</span>
            </div>
            <span className={`state-label ${corpus.state}`}>
              <i /> {corpus.semantic_available ? "semantic ready" : corpus.state}
            </span>
            <small>{compactNumber(corpus.symbols)} symbols · {compactNumber(corpus.references)} refs</small>
          </article>
        ))}
      </div>
    </Panel>
  );
}

function ChartEmpty({ message }: { message: string }) {
  return (
    <div className="chart-empty">
      <Icon name="activity" size={24} />
      <strong>Waiting for telemetry</strong>
      <span>{message}</span>
    </div>
  );
}

function linePath<T>(
  points: T[],
  x: (index: number) => number,
  value: (item: T) => number | null,
  y: (value: number) => number,
): string {
  let path = "";
  let drawing = false;
  points.forEach((point, index) => {
    const current = value(point);
    if (current === null) {
      drawing = false;
      return;
    }
    path += `${drawing ? "L" : "M"}${x(index).toFixed(1)},${y(current).toFixed(1)} `;
    drawing = true;
  });
  return path.trim();
}

function tickIndexes(length: number, count: number): number[] {
  if (!length) return [];
  const indexes = new Set<number>();
  for (let index = 0; index < count; index += 1) {
    indexes.add(Math.round((index / Math.max(count - 1, 1)) * (length - 1)));
  }
  return [...indexes];
}
