import { useMemo, useRef, useState } from "react";

import { Icon } from "./Icons";
import { Panel } from "./Charts";
import {
  filterLogs,
  formatDuration,
  formatTime,
  toolLabel,
} from "../lib/format";
import type { Alert, LogLevel, LogRow, RecentQuery, Summary } from "../types";

export function RecentQueries({ queries }: { queries: RecentQuery[] }) {
  const [selected, setSelected] = useState<string | null>(() =>
    queries.reduce<RecentQuery | null>(
      (slowest, item) => (slowest === null || item.duration_ms > slowest.duration_ms ? item : slowest),
      null,
    )?.id ?? null,
  );
  return (
    <Panel title="Recent queries" subtitle="Latest MCP calls with result and latency context" className="recent-panel">
      {queries.length ? (
        <div className="recent-table" role="table" aria-label="Recent MCP queries">
          <div className="recent-head" role="row">
            <span>Time</span>
            <span>Query</span>
            <span>Tool</span>
            <span>Latency</span>
            <span>Status</span>
          </div>
          {queries.slice(0, 8).map((query) => {
            const open = selected === query.id;
            return (
              <div className={open ? "recent-record open" : "recent-record"} key={query.id}>
                <button type="button" role="row" onClick={() => setSelected(open ? null : query.id)} aria-expanded={open}>
                  <span className="mono">{formatTime(query.timestamp, true)}</span>
                  <span className="query-cell" title={query.query}>{query.query}</span>
                  <span className="tool-cell">{toolLabel(query.tool)}</span>
                  <strong className={query.duration_ms >= 2_000 ? "latency-hot" : ""}>{formatDuration(query.duration_ms)}</strong>
                  <span className={`query-status ${query.status}`}>{query.status}</span>
                  <Icon name="chevron" size={16} />
                </button>
                {open ? (
                  <div className="query-detail">
                    <span><small>Target</small><strong>{query.target}</strong></span>
                    <span><small>Results</small><strong>{query.result_count}</strong></span>
                    <span><small>Retrieval</small><strong>{query.degraded ? "Degraded" : "Nominal"}</strong></span>
                    <span><small>Request ID</small><code>{query.id}</code></span>
                    {query.error_code ? <span><small>Error</small><strong>{query.error_code}</strong></span> : null}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : (
        <div className="table-empty"><Icon name="search" /><span>No queries in this time window.</span></div>
      )}
    </Panel>
  );
}

interface LiveLogsProps {
  logs: LogRow[];
  live: boolean;
  source: string;
  onLiveChange: (live: boolean) => void;
  onSourceChange: (source: string) => void;
}

export function LiveLogs({ logs, live, source, onLiveChange, onSourceChange }: LiveLogsProps) {
  const [query, setQuery] = useState("");
  const [level, setLevel] = useState<LogLevel | "ALL">("ALL");
  const [expanded, setExpanded] = useState<string | null>(
    logs.find((log) => log.level === "ERROR")?.id ?? logs[0]?.id ?? null,
  );
  const searchRef = useRef<HTMLInputElement>(null);
  const sources = useMemo(() => [...new Set(logs.map((log) => log.source))].sort(), [logs]);
  const filtered = useMemo(() => filterLogs(logs, query, level, source), [logs, query, level, source]);

  return (
    <Panel
      id="logs"
      title="Live logs"
      subtitle="Structured MCP execution events retained in memory"
      className="logs-panel"
      action={
        <label className="switch-control">
          <input type="checkbox" checked={live} onChange={(event) => onLiveChange(event.target.checked)} />
          <span />
          Autoscroll
        </label>
      }
    >
      <div className="log-toolbar">
        <label className="search-field">
          <Icon name="search" size={16} />
          <input
            ref={searchRef}
            type="search"
            placeholder="Search logs…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            aria-label="Search logs"
          />
          <kbd>⌘K</kbd>
        </label>
        <div className="segmented level-filter" aria-label="Log level">
          {(["ALL", "INFO", "WARN", "ERROR"] as const).map((item) => (
            <button key={item} type="button" className={level === item ? `active ${item.toLocaleLowerCase()}` : ""} onClick={() => setLevel(item)}>
              {item !== "ALL" ? <i className={`level-dot ${item.toLocaleLowerCase()}`} /> : null}
              {item}
            </button>
          ))}
        </div>
        <label className="select-field">
          <span>Tool</span>
          <select
            aria-label="Filter logs by tool"
            data-testid="log-tool-filter"
            value={source}
            onChange={(event) => onSourceChange(event.target.value)}
          >
            <option value="">All tools</option>
            {sources.map((item) => <option value={item} key={item}>{toolLabel(item)}</option>)}
          </select>
        </label>
        {(query || level !== "ALL" || source) ? (
          <button className="clear-button" type="button" onClick={() => { setQuery(""); setLevel("ALL"); onSourceChange(""); }}>
            Clear filters
          </button>
        ) : null}
      </div>
      <LogTable rows={filtered} expanded={expanded} onExpandedChange={setExpanded} />
    </Panel>
  );
}

function LogTable({ rows, expanded, onExpandedChange }: { rows: LogRow[]; expanded: string | null; onExpandedChange: (id: string | null) => void }) {
  if (!rows.length) {
    return <div className="table-empty"><Icon name="logs" /><span>No logs match the active filters.</span></div>;
  }
  return (
    <div className="log-table" role="table" aria-label="Structured service logs">
      <div className="log-head" role="row">
        <span>Time</span><span>Level</span><span>Tool</span><span>Duration</span><span>Message</span>
      </div>
      {rows.map((log) => {
        const open = expanded === log.id;
        return (
          <div className={`log-record ${log.level.toLocaleLowerCase()}${open ? " open" : ""}`} key={log.id}>
            <button type="button" role="row" onClick={() => onExpandedChange(open ? null : log.id)} aria-expanded={open}>
              <time className="mono">{formatTime(log.timestamp, true)}</time>
              <span className={`log-level ${log.level.toLocaleLowerCase()}`}>{log.level}</span>
              <span>{toolLabel(log.source)}</span>
              <strong>{formatDuration(log.duration_ms)}</strong>
              <span className="log-message">{log.message}</span>
              <Icon name="chevron" size={16} />
            </button>
            {open ? <LogDetail log={log} /> : null}
          </div>
        );
      })}
    </div>
  );
}

function LogDetail({ log }: { log: LogRow }) {
  const [copied, setCopied] = useState(false);
  const payload = JSON.stringify(log.details, null, 2);
  const copy = async () => {
    await navigator.clipboard.writeText(payload);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1_500);
  };
  return (
    <div className="log-detail">
      <pre>{payload}</pre>
      <button type="button" onClick={() => void copy()}>
        <Icon name={copied ? "check" : "copy"} size={15} /> {copied ? "Copied" : "Copy JSON"}
      </button>
    </div>
  );
}

interface AlertsRailProps {
  alerts: Alert[];
  summary: Summary;
  onInspect: (alert: Alert) => void;
}

export function AlertsRail({ alerts, summary, onInspect }: AlertsRailProps) {
  return (
    <aside id="incidents" className="alerts-rail" aria-label="Active incidents">
      <header>
        <div>
          <span className="rail-kicker">System watch</span>
          <h2>Active incidents</h2>
        </div>
        <span className={alerts.length ? "incident-count active" : "incident-count"}>{alerts.length}</span>
      </header>
      {alerts.length ? (
        <div className="incident-list">
          {alerts.map((alert) => (
            <button className={`incident-card ${alert.severity}`} type="button" key={alert.id} onClick={() => onInspect(alert)}>
              <span className="incident-icon"><Icon name="alert" size={16} /></span>
              <span>
                <strong>{alert.title}</strong>
                <small>{alert.message}</small>
              </span>
              <Icon name="chevron" size={15} />
            </button>
          ))}
        </div>
      ) : (
        <div className="incident-empty">
          <span><Icon name="check" /></span>
          <strong>Clear horizon</strong>
          <small>No active incidents in this window.</small>
        </div>
      )}
      <section className="rail-section">
        <h3>Window diagnostics</h3>
        <dl>
          <div><dt>Failed calls</dt><dd className={summary.error_count ? "error-text" : ""}>{summary.error_count}</dd></div>
          <div><dt>Degraded calls</dt><dd>{summary.degraded_count}</dd></div>
          <div><dt>Success rate</dt><dd>{summary.success_rate.toFixed(2)}%</dd></div>
          <div><dt>P50 latency</dt><dd>{formatDuration(summary.p50_latency_ms)}</dd></div>
        </dl>
      </section>
      <section className="rail-section rail-note">
        <Icon name="memory" size={17} />
        <p>Telemetry is process-local and bounded. Restarting the MCP service clears this history.</p>
      </section>
    </aside>
  );
}
