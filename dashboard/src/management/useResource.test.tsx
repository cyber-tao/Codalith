import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useResource } from "./useResource";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("management polling", () => {
  it("waits for a pending response before scheduling another poll and aborts on unmount", async () => {
    vi.useFakeTimers();
    let resolve: ((response: Response) => void) | undefined;
    let latestSignal: AbortSignal | undefined;
    const fetchMock = vi.fn((_url: unknown, init: RequestInit) => {
      latestSignal = init.signal as AbortSignal;
      return new Promise<Response>((done) => { resolve = done; });
    });
    vi.stubGlobal("fetch", fetchMock);
    const { result, unmount } = renderHook(() => useResource<{ status: string }>("jobs/job-1", "test-token", 2_000));
    await act(async () => { await vi.advanceTimersByTimeAsync(12_000); });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => { resolve!(new Response(JSON.stringify({ status: "running" }))); });
    expect(result.current.data?.status).toBe("running");
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    unmount();
    expect(latestSignal?.aborted).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("discards data from an earlier request after the selected target changes", async () => {
    const completions = new Map<string, (response: Response) => void>();
    vi.stubGlobal("fetch", vi.fn((url: string) => new Promise<Response>((done) => { completions.set(url, done); })));
    const { result, rerender } = renderHook(({ job }) => useResource<{ id: string }>(`jobs/${job}`, "test-token"), { initialProps: { job: "old" } });
    rerender({ job: "new" });
    await act(async () => { completions.get("/api/admin/jobs/new")!(new Response(JSON.stringify({ id: "new" }))); });
    expect(result.current.data?.id).toBe("new");
    await act(async () => { completions.get("/api/admin/jobs/old")!(new Response(JSON.stringify({ id: "old" }))); });
    expect(result.current.data?.id).toBe("new");
  });
});
