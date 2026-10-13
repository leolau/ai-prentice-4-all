import { afterEach, describe, expect, it, vi } from "vitest";

import { HermesApiClient, SESSION_PAGE_SIZE } from "@/lib/api/client";

afterEach(() => {
  vi.restoreAllMocks();
});

function ok(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

const row = (id: string) => ({ id, source: "cli", started_at: 0 });

/** A fake upstream holding `ids` (recent-first) that pages by limit/offset,
 * optionally keeping only `keep` after paging (like its tag filter). */
function upstream(ids: string[], keep?: (id: string) => boolean) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = new URL(String(input));
    const limit = Number(url.searchParams.get("limit"));
    const offset = Number(url.searchParams.get("offset") ?? 0);
    const page = ids.slice(offset, offset + limit).filter((id) => !keep || keep(id));
    return ok({ sessions: page.map(row), total: ids.length, limit, offset });
  });
}

describe("HermesApiClient.allSessions", () => {
  it("reads every page, not just the newest page", async () => {
    const ids = Array.from({ length: SESSION_PAGE_SIZE * 2 + 25 }, (_, i) => `s${i}`);
    const fetchMock = upstream(ids);
    const client = new HermesApiClient({ baseUrl: "http://api.test" });

    const res = await client.allSessions({ order: "recent", excludeSources: "cron" });

    expect(res.sessions.map((s) => s.id)).toEqual(ids);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    const urls = fetchMock.mock.calls.map((c) => new URL(String(c[0])));
    expect(urls.map((u) => u.searchParams.get("offset"))).toEqual([null, "200", "400"]);
    expect(urls.every((u) => u.searchParams.get("exclude_sources") === "cron")).toBe(true);
  });

  it("keeps paging past a page the tag filter emptied", async () => {
    const ids = Array.from({ length: SESSION_PAGE_SIZE + 3 }, (_, i) => `s${i}`);
    upstream(ids, (id) => id === "s0" || Number(id.slice(1)) >= SESSION_PAGE_SIZE);
    const client = new HermesApiClient({ baseUrl: "http://api.test" });

    const res = await client.allSessions({ tags: "x" });

    expect(res.sessions.map((s) => s.id)).toEqual(["s0", "s200", "s201", "s202"]);
  });

  it("drops a row repeated across pages when activity reorders the list", async () => {
    let call = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => {
      call += 1;
      const ids =
        call === 1
          ? Array.from({ length: SESSION_PAGE_SIZE }, (_, i) => `s${i}`)
          : ["s199", "s200"];
      return ok({ sessions: ids.map(row), total: SESSION_PAGE_SIZE + 1 });
    });
    const client = new HermesApiClient({ baseUrl: "http://api.test" });

    const res = await client.allSessions();

    expect(res.sessions).toHaveLength(SESSION_PAGE_SIZE + 1);
    expect(new Set(res.sessions.map((s) => s.id)).size).toBe(SESSION_PAGE_SIZE + 1);
  });
});
