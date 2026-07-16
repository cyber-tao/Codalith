import { describe, expect, it } from "vitest";

import { filterLogs, formatDuration, toolLabel } from "./format";
import type { LogRow } from "../types";

const LOGS: LogRow[] = [
  {
    id: "1",
    timestamp: "2026-07-17T00:00:00Z",
    level: "INFO",
    source: "codalith_search",
    message: "Search completed",
    target: "ue-5.7.4",
    duration_ms: 42,
    details: { query: "UWorld" },
  },
  {
    id: "2",
    timestamp: "2026-07-17T00:00:01Z",
    level: "ERROR",
    source: "codalith_graph",
    message: "Graph failed",
    target: "ue-5.7.4",
    duration_ms: 1200,
    details: { error_code: "timeout" },
  },
];

describe("dashboard formatting", () => {
  it("formats tool names and durations for dense UI", () => {
    expect(toolLabel("codalith_find_symbol")).toBe("find symbol");
    expect(formatDuration(482)).toBe("482 ms");
    expect(formatDuration(2_340)).toBe("2.34 s");
  });

  it("filters logs by level, source, and searchable details", () => {
    expect(filterLogs(LOGS, "uworld", "ALL", "")).toHaveLength(1);
    expect(filterLogs(LOGS, "", "ERROR", "codalith_graph")).toEqual([LOGS[1]]);
    expect(filterLogs(LOGS, "timeout", "ALL", "")[0]?.id).toBe("2");
  });
});
