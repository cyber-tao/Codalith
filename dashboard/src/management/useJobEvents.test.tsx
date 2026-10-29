import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { JobEvent } from "./types";
import { useJobEvents } from "./useJobEvents";

function events(first: number, last: number, jobId = "job-1"): JobEvent[] {
  return Array.from({ length: last - first + 1 }, (_, index) => ({
    seq: first + index, job_id: jobId, at: "2026-10-29T00:00:00Z", type: "progress", payload: {},
  }));
}

function response(items: JobEvent[]) {
  return new Response(JSON.stringify({ items }));
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("job event polling", () => {
  it("starts with the latest 500 events, then follows the cursor without duplicate entries", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(events(502, 1001)))
      .mockResolvedValueOnce(response(events(1001, 1003)))
      .mockResolvedValueOnce(response([]));
    vi.stubGlobal("fetch", fetchMock);
    const { result, rerender } = renderHook(({ interval }) => useJobEvents("job-1", "token", interval), { initialProps: { interval: 2_000 } });
    await act(async () => {});
    expect(fetchMock.mock.calls[0][0]).toBe("/api/admin/jobs/job-1/events?tail=500");
    expect(result.current.events.map((event) => event.seq)).toEqual(Array.from({ length: 500 }, (_, index) => index + 502));
    rerender({ interval: 5_000 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(fetchMock.mock.calls[1][0]).toBe("/api/admin/jobs/job-1/events?after_seq=1001");
    expect(result.current.events.map((event) => event.seq)).toEqual(Array.from({ length: 500 }, (_, index) => index + 504));
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    expect(fetchMock.mock.calls[2][0]).toBe("/api/admin/jobs/job-1/events?after_seq=1003");
  });

  it("aborts old job and token requests and rejects their late responses", async () => {
    const pending: Array<{ done: (value: Response) => void; signal: AbortSignal }> = [];
    const fetchMock = vi.fn((_url: string, init: RequestInit) => new Promise<Response>((done) => {
      pending.push({ done, signal: init.signal as AbortSignal });
    }));
    vi.stubGlobal("fetch", fetchMock);
    const { result, rerender, unmount } = renderHook(({ job, token }) => useJobEvents(job, token, 2_000), { initialProps: { job: "old", token: "first" } });
    rerender({ job: "new", token: "first" });
    expect(pending[0].signal.aborted).toBe(true);
    rerender({ job: "new", token: "second" });
    expect(pending[1].signal.aborted).toBe(true);
    await act(async () => { pending[2].done(response(events(8, 9, "new"))); });
    await act(async () => {
      pending[0].done(response(events(1, 2, "old")));
      pending[1].done(response(events(3, 4, "new")));
    });
    expect(result.current.events.map((event) => event.seq)).toEqual([8, 9]);
    expect(fetchMock.mock.calls.every(([url]) => url.endsWith("?tail=500"))).toBe(true);
    unmount();
    expect(pending[2].signal.aborted).toBe(true);
  });
});
