import { useEffect, useState } from "react";

import { AlertsRail, LiveLogs, RecentQueries } from "./components/Activity";
import { CorpusFootprint, QueryTrafficChart, ResourcePressure, ToolUsage } from "./components/Charts";
import { MobileNavigation, Sidebar, Topbar } from "./components/Chrome";
import { Icon } from "./components/Icons";
import { MetricStrip, StatusBanner } from "./components/Metrics";
import { useDashboard } from "./hooks/useDashboard";
import type { Alert, WindowKey } from "./types";

export default function App() {
  const [range, setRange] = useState<WindowKey>("1h");
  const [target, setTarget] = useState("");
  const [live, setLive] = useState(true);
  const [activeSection, setActiveSection] = useState("overview");
  const [logSource, setLogSource] = useState("");
  const { data, error, loading, refreshing, refresh } = useDashboard(range, target, live);

  useEffect(() => {
    document.title = data ? `${data.service.target} · Codalith Operations` : "Codalith Operations";
  }, [data]);

  const navigate = (section: string) => {
    setActiveSection(section);
    document.getElementById(section)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const selectTool = (tool: string) => {
    setLogSource(tool);
    navigate("logs");
  };

  const inspectAlert = (_alert?: Alert) => navigate("logs");

  const exportSnapshot = () => {
    if (!data) return;
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `codalith-${data.service.target}-${data.window.key}-${data.generated_at.slice(0, 19).replaceAll(":", "-")}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="app-shell">
      <Sidebar snapshot={data} activeSection={activeSection} onNavigate={navigate} />
      <div className="main-shell">
        <Topbar
          snapshot={data}
          range={range}
          target={target}
          live={live}
          refreshing={refreshing}
          onRangeChange={setRange}
          onTargetChange={setTarget}
          onLiveChange={setLive}
          onRefresh={() => void refresh()}
          onExport={exportSnapshot}
        />
        {error && !data ? (
          <ErrorState message={error} onRetry={() => void refresh()} />
        ) : loading || !data ? (
          <DashboardSkeleton />
        ) : (
          <div className="dashboard-body">
            <main id="overview" className="dashboard-canvas">
              {error ? (
                <div className="stale-banner" role="status">
                  <Icon name="alert" />
                  Live refresh failed: {error}. Showing the last successful snapshot.
                  <button type="button" onClick={() => void refresh()}>Retry</button>
                </div>
              ) : null}
              {data.alerts.length ? (
                <button className="mobile-incident-strip" type="button" onClick={() => inspectAlert(data.alerts[0])}>
                  <Icon name="alert" />
                  <span><strong>{data.alerts[0].title}</strong>{data.alerts[0].message}</span>
                  <Icon name="chevron" />
                </button>
              ) : null}
              <StatusBanner snapshot={data} onInspect={() => navigate("incidents")} />
              <MetricStrip snapshot={data} />
              <div className="overview-grid">
                <QueryTrafficChart series={data.series} />
                <ToolUsage tools={data.tools} onSelect={selectTool} />
                <ResourcePressure series={data.series} />
                <CorpusFootprint corpora={data.corpora} />
                <RecentQueries queries={data.recent_queries} />
                <LiveLogs
                  logs={data.logs}
                  live={live}
                  source={logSource}
                  onLiveChange={setLive}
                  onSourceChange={setLogSource}
                />
              </div>
              <footer className="dashboard-footer">
                <span><span className="status-dot live" /> MCP HTTP telemetry</span>
                <span>{data.retention.events_stored.toLocaleString()} / {data.retention.event_capacity.toLocaleString()} events retained</span>
                <span>Snapshot generated {new Date(data.generated_at).toLocaleString()}</span>
              </footer>
            </main>
            <AlertsRail alerts={data.alerts} summary={data.summary} onInspect={inspectAlert} />
          </div>
        )}
      </div>
      <MobileNavigation activeSection={activeSection} onNavigate={navigate} />
    </div>
  );
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <main className="error-state">
      <span><Icon name="alert" size={26} /></span>
      <h1>Dashboard unavailable</h1>
      <p>{message}</p>
      <button type="button" onClick={onRetry}><Icon name="refresh" /> Retry connection</button>
      <small>Check that the Codalith HTTP service is running and the Vite proxy targets the same port.</small>
    </main>
  );
}

function DashboardSkeleton() {
  return (
    <main className="dashboard-skeleton" aria-label="Loading dashboard">
      <div className="skeleton-line wide" />
      <div className="skeleton-metrics">{Array.from({ length: 4 }, (_, index) => <div key={index} />)}</div>
      <div className="skeleton-panels"><div /><div /><div /><div /></div>
    </main>
  );
}
