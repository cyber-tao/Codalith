import { useState } from "react";

import { BrandMark, Icon, type IconName } from "./Icons";
import { formatTime, formatUptime } from "../lib/format";
import type { DashboardSnapshot, WindowKey } from "../types";

interface NavigationItem {
  id: string;
  label: string;
  icon: IconName;
}

export const NAVIGATION: NavigationItem[] = [
  { id: "overview", label: "Overview", icon: "overview" },
  { id: "queries", label: "Queries", icon: "search" },
  { id: "performance", label: "Performance", icon: "performance" },
  { id: "logs", label: "Logs", icon: "logs" },
  { id: "corpora", label: "Corpora", icon: "database" },
];

interface SidebarProps {
  snapshot: DashboardSnapshot | null;
  activeSection: string;
  onNavigate: (section: string) => void;
}

export function Sidebar({ snapshot, activeSection, onNavigate }: SidebarProps) {
  return (
    <aside className="sidebar" aria-label="Primary navigation">
      <div className="brand">
        <BrandMark />
        <span>
          Codalith
          <strong>Operations</strong>
        </span>
      </div>
      <nav className="side-nav">
        {NAVIGATION.map((item) => (
          <button
            className={activeSection === item.id ? "nav-item active" : "nav-item"}
            key={item.id}
            onClick={() => onNavigate(item.id)}
            type="button"
          >
            <Icon name={item.icon} />
            <span>{item.label}</span>
          </button>
        ))}
        <button className="nav-item" type="button" disabled title="Coming soon">
          <Icon name="settings" />
          <span>Settings</span>
        </button>
      </nav>
      <div className="service-meta">
        <span className="meta-label">Service</span>
        <div className="service-line">
          <span className={snapshot?.service.ready ? "status-dot live" : "status-dot error"} />
          MCP Service
          <span>v{snapshot?.service.version ?? "—"}</span>
        </div>
        <span className="meta-label">Target</span>
        <strong>{snapshot?.service.target ?? "Connecting"}</strong>
        <span className="meta-label">Uptime</span>
        <strong>{snapshot ? formatUptime(snapshot.service.uptime_seconds) : "—"}</strong>
      </div>
    </aside>
  );
}

interface TopbarProps {
  snapshot: DashboardSnapshot | null;
  range: WindowKey;
  target: string;
  live: boolean;
  refreshing: boolean;
  onRangeChange: (range: WindowKey) => void;
  onTargetChange: (target: string) => void;
  onLiveChange: (live: boolean) => void;
  onRefresh: () => void;
  onExport: () => void;
}

const RANGES: WindowKey[] = ["15m", "1h", "6h", "24h", "7d"];

export function Topbar({
  snapshot,
  range,
  target,
  live,
  refreshing,
  onRangeChange,
  onTargetChange,
  onLiveChange,
  onRefresh,
  onExport,
}: TopbarProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <header className="topbar">
      <div className="mobile-brand">
        <BrandMark size={30} />
        <strong>Codalith Operations</strong>
      </div>
      <label className="target-select">
        <span className="sr-only">Corpus or workspace</span>
        <Icon name="database" size={16} />
        <select value={target || snapshot?.service.target || ""} onChange={(event) => onTargetChange(event.target.value)}>
          {snapshot?.targets.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label}
            </option>
          ))}
        </select>
      </label>
      <div className="range-control" aria-label="Time range">
        {RANGES.map((item) => (
          <button
            key={item}
            type="button"
            className={range === item ? "active" : ""}
            onClick={() => onRangeChange(item)}
          >
            {item}
          </button>
        ))}
      </div>
      <div className="topbar-actions">
        <button
          className={live ? "live-control active" : "live-control"}
          type="button"
          onClick={() => onLiveChange(!live)}
          aria-pressed={live}
        >
          <span className={live ? "status-dot live pulse" : "status-dot"} />
          {live ? "Live" : "Paused"}
          <Icon name={live ? "pause" : "play"} size={15} />
        </button>
        <button className="icon-button" type="button" onClick={onRefresh} aria-label="Refresh dashboard">
          <Icon name="refresh" className={refreshing ? "spinning" : ""} />
        </button>
        <button className="icon-button desktop-only" type="button" onClick={onExport} aria-label="Export snapshot">
          <Icon name="download" />
        </button>
        <button className="icon-button mobile-menu-button" type="button" onClick={() => setMenuOpen(!menuOpen)} aria-label="Open navigation">
          <Icon name={menuOpen ? "close" : "menu"} />
        </button>
      </div>
      <div className="freshness">
        <span>{snapshot ? `Updated ${formatTime(snapshot.generated_at, true)}` : "Connecting"}</span>
        <strong className={snapshot?.service.ready ? "healthy" : "unhealthy"}>
          <span className={snapshot?.service.ready ? "status-dot live" : "status-dot error"} />
          {snapshot?.service.ready ? "Healthy" : "Attention"}
        </strong>
      </div>
      {menuOpen ? (
        <div className="mobile-menu">
          {NAVIGATION.map((item) => (
            <a key={item.id} href={`#${item.id}`} onClick={() => setMenuOpen(false)}>
              <Icon name={item.icon} />
              {item.label}
            </a>
          ))}
          <button type="button" onClick={onExport}>
            <Icon name="download" /> Export snapshot
          </button>
        </div>
      ) : null}
    </header>
  );
}

export function MobileNavigation({ activeSection, onNavigate }: Omit<SidebarProps, "snapshot">) {
  return (
    <nav className="mobile-bottom-nav" aria-label="Mobile navigation">
      {NAVIGATION.slice(0, 4).map((item) => (
        <button
          key={item.id}
          type="button"
          className={activeSection === item.id ? "active" : ""}
          onClick={() => onNavigate(item.id)}
        >
          <Icon name={item.icon} />
          <span>{item.label}</span>
        </button>
      ))}
    </nav>
  );
}
