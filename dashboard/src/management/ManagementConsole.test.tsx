import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { ManagementConsole } from "./ManagementConsole";
import type { BuildJob, ManagementConfig, SourceTag } from "./types";

const CONFIG: ManagementConfig = {
  enabled: true,
  storage_root: "/data/codalith",
  source_roots: [{ root_id: "0", path: "/srv/sources", label: "Sources" }],
  profiles: [{ profile_id: "ue", label: "UE source", adapter: "cpp-ue", embedding_provider: "fake", embedding_model: "test-embedding" }],
  max_selected_tags: 10,
  legacy_generations: [{ generation_id: "old-1", corpus_id: "ue-old", embedding_model: "test-embedding" }],
};

const TAGS: SourceTag[] = [
  { tag: "5.7.4-release", tag_oid: "a".repeat(40), commit: "1".repeat(40), annotated: true, engine_version: "5.7.4" },
  { tag: "5.8.0-release", tag_oid: "b".repeat(40), commit: "2".repeat(40), annotated: false, engine_version: "5.8.0" },
];

function createJob(): BuildJob {
  return {
    job_id: "job-1", database_id: "db-1", repo_id: "repo-1", plan_id: "plan-1",
    status: "running", phase: "embedding", completed_units: 0, total_units: 2,
    unit: "tags", completed_tags: 0, total_tags: 2,
    created_at: "2026-10-29T00:00:00Z", updated_at: "2026-10-29T00:00:01Z",
    items: TAGS.map((tag, index) => ({ ...tag, target_id: `ue-${index}`, status: index ? "queued" : "running", phase: index ? "queued" : "embedding", completed_units: index ? 0 : 3, total_units: index ? null : 8, unit: "chunks" })),
  };
}

function response(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
}

function Harness() {
  const [view, setView] = useState<"databases" | "build-jobs">("databases");
  const [token, setToken] = useState("admin-test");
  return <ManagementConsole view={view} token={token} onTokenChange={setToken} onNavigate={setView} />;
}

beforeEach(() => {
  window.history.replaceState(null, "", "/dashboard/");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("database administration", () => {
  it("opens a bookmarked management page without requiring a monitoring snapshot", async () => {
    window.history.replaceState(null, "", "/dashboard/#databases");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await screen.findByRole("heading", { name: "Connect as administrator" });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByRole("combobox", { name: "Corpus or workspace" })).not.toBeInTheDocument();
  });

  it("opens management when the monitoring snapshot is unavailable and keeps the token out of storage", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.startsWith("/api/dashboard/")) return response({ error: { message: "No default target is ready" } }, 503);
      if (path === "/api/admin/config") return response(CONFIG);
      return response({ items: [] });
    });
    vi.stubGlobal("fetch", fetchMock);
    const localWrite = vi.spyOn(Storage.prototype, "setItem");
    render(<App />);
    fireEvent.click(screen.getAllByRole("button", { name: "Databases" })[0]);
    await screen.findByRole("heading", { name: "Connect as administrator" });
    fireEvent.change(screen.getByLabelText("Administrator token"), { target: { value: "admin-test" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    await screen.findByRole("heading", { name: "1. Source repository" });
    expect(localWrite).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("admin-test"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    expect(await screen.findByLabelText("Administrator token")).toHaveValue("");
  });

  it("preserves tag selection across pages, submits the pinned preflight, and shows real job progress", async () => {
    const submitted: Array<{ path: string; body: Record<string, unknown> }> = [];
    let job: BuildJob | null = null;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const path = url.pathname;
      if (init?.method === "POST") {
        const body = init.body ? JSON.parse(String(init.body)) as Record<string, unknown> : {};
        submitted.push({ path, body });
        if (path.endsWith("/build-plans")) return response({ plan_id: "plan-1", database_id: "db-1", repo_id: "repo-1", created_at: "2026-10-29T00:00:00Z", items: TAGS.map((tag, index) => ({ ...tag, target_id: `ue-${index}` })), summary: { version_count: 2, file_occurrences: 4, logical_source_bytes: 2048, unique_source_bytes: 1024, shared_file_occurrences: 2, embedding_estimate: null, notes: ["Embedding calls depend on cache matches"] } });
        if (path.endsWith("/jobs")) { job = createJob(); return response(job, 202); }
        if (path.endsWith("/cancel")) { job = { ...job!, status: "cancelled", phase: "cancelled" }; return response(job); }
      }
      if (path.endsWith("/config")) return response(CONFIG);
      if (path.endsWith("/repositories")) return response({ items: [{ repo_id: "repo-1", name: "UE", path: "/srv/sources/UE" }] });
      if (path.endsWith("/databases")) return response({ items: [{ database_id: "db-1", name: "Unreal Engine", profile_id: "ue", repo_id: "repo-1", created_at: "2026-10-29T00:00:00Z" }] });
      if (path.endsWith("/space")) return response({ physical_source_bytes: null, logical_source_bytes: null, vector_count: null, breakdown: { vector_bytes: 1048576, structure_bytes: null }, notes: ["Not measured yet"] });
      if (path.endsWith("/tags")) {
        const index = url.searchParams.get("cursor") === "1" ? 1 : 0;
        expect(url.searchParams.get("cursor")).not.toBe("");
        return response({ items: [TAGS[index]], next_cursor: index ? null : "1", total: 2 });
      }
      if (path.endsWith("/events")) return response({ items: [{ seq: 1, job_id: "job-1", at: "2026-10-29T00:00:01Z", type: "progress", payload: { message: "Embedding 3 of 8 chunks" } }] });
      if (path.endsWith("/jobs/job-1")) return response(job);
      if (path.endsWith("/jobs")) return response({ items: job ? [job] : [] });
      return response({ error: { message: `Unexpected path ${path}` } }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<Harness />);
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select 5.7.4-release" }));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select 5.8.0-release" }));
    expect(screen.getByText("2 / 10 selected")).toBeVisible();
    fireEvent.change(screen.getByLabelText("Reuse a legacy generation"), { target: { value: "old-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Preflight selected tags" }));
    await screen.findByRole("heading", { name: "4. Review and build" });
    expect(screen.getByText("Embedding calls depend on cache matches")).toBeVisible();
    expect(screen.getByText("1 MiB")).toBeVisible();
    expect(submitted[0]).toEqual({ path: "/api/admin/build-plans", body: { database_id: "db-1", repo_id: "repo-1", tags: ["5.7.4-release", "5.8.0-release"], legacy_generation: "old-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Start build · 2 versions" }));
    await screen.findByRole("heading", { name: "Build details" });
    expect(await screen.findByRole("progressbar", { name: "Published versions" })).toHaveAttribute("value", "0");
    expect(screen.getByRole("progressbar", { name: "5.7.4-release phase progress" })).toHaveAttribute("value", "37.5");
    expect(screen.queryByRole("progressbar", { name: "5.8.0-release phase progress" })).not.toBeInTheDocument();
    await screen.findByText("Embedding 3 of 8 chunks");
    expect(submitted[1].body.plan_id).toBe("plan-1");
    expect(submitted[1].body.idempotency_key).toEqual(expect.any(String));
    fireEvent.click(screen.getByRole("button", { name: "Cancel remaining work" }));
    await screen.findByRole("button", { name: "Continue unfinished versions" });
  });

  it("offers retry for the backend partial-success state", async () => {
    const job = { ...createJob(), status: "partial" as const, phase: "partial", completed_tags: 1 };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname.endsWith("/config")) return response(CONFIG);
      if (url.pathname.endsWith("/events")) return response({ items: [] });
      if (url.pathname.endsWith("/jobs/job-1")) return response(job);
      if (url.pathname.endsWith("/jobs")) return response({ items: [job] });
      return response({ items: [] });
    }));
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "Build jobs" }));
    fireEvent.click(await screen.findByRole("button", { name: /job-1/ }));
    expect(await screen.findByRole("button", { name: "Retry failed versions" })).toBeEnabled();
  });

  it("shows server-measured elapsed time for index maintenance without inventing a percentage", async () => {
    const job = createJob();
    job.updated_at = "2026-10-29T00:01:11Z";
    job.items[0] = { ...job.items[0], phase: "index", total_units: null, completed_units: 0, phase_started_at: "2026-10-29T00:00:01Z" };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname.endsWith("/config")) return response(CONFIG);
      if (url.pathname.endsWith("/events")) return response({ items: [] });
      if (url.pathname.endsWith("/jobs/job-1")) return response(job);
      if (url.pathname.endsWith("/jobs")) return response({ items: [job] });
      return response({ items: [] });
    }));
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "Build jobs" }));
    fireEvent.click(await screen.findByRole("button", { name: /job-1/ }));
    expect(await screen.findByText(/Elapsed 1m 10s/)).toBeVisible();
    expect(screen.queryByRole("progressbar", { name: "5.7.4-release phase progress" })).not.toBeInTheDocument();
  });

  it("browses server roots and registers the selected repository without a local file upload", async () => {
    const registered: unknown[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname.endsWith("/config")) return response(CONFIG);
      if (url.pathname.endsWith("/directories")) {
        expect(url.searchParams.get("root_id")).toBe("0");
        return response({ root_id: "0", path: ".", items: [{ name: "UE", path: "UE", is_repository: true }] });
      }
      if (url.pathname.endsWith("/repositories") && init?.method === "POST") {
        registered.push(JSON.parse(String(init.body)));
        return response({ repo_id: "repo-1", name: "UE", path: "/srv/sources/UE" });
      }
      if (url.pathname.endsWith("/tags")) return response({ items: [], next_cursor: null, total: 0 });
      return response({ items: [] });
    }));
    render(<Harness />);
    fireEvent.click(await screen.findByRole("button", { name: "Browse server directories" }));
    fireEvent.click(await screen.findByRole("button", { name: "Use repository" }));
    await waitFor(() => expect(registered).toEqual([{ root_id: "0", path: "UE" }]));
    expect(screen.queryByLabelText(/upload/i)).not.toBeInTheDocument();
  });
});
