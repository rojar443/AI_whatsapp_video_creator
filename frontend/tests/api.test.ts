import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Call = { url: string; init: RequestInit & { headers: Headers } };

function mockFetch(...responses: Array<{ status?: number; body?: string }>) {
  const calls: Call[] = [];
  const fn = vi.fn(async (url: string, init: any) => {
    calls.push({ url, init });
    const r = responses.shift() ?? { status: 200, body: "{}" };
    return new Response(r.body ?? "", { status: r.status ?? 200 });
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

// Fresh module per test: the CSRF fallback lives in module state.
async function load() {
  vi.resetModules();
  return import("@/lib/api");
}

beforeEach(() => vi.unstubAllGlobals());
afterEach(() => vi.unstubAllGlobals());

describe("api()", () => {
  it("GETs same-origin without a CSRF header and parses JSON", async () => {
    const { api } = await load();
    const calls = mockFetch({ body: '{"ok":true}' });
    expect(await api("/api/me")).toEqual({ ok: true });
    expect(calls[0].init.method).toBe("GET");
    expect(calls[0].init.credentials).toBe("same-origin");
    expect(calls[0].init.headers.has("x-csrf-token")).toBe(false);
  });

  it("sends JSON bodies and the CSRF cookie on writes", async () => {
    const { api } = await load();
    document.cookie = "rc_csrf=tok%2B1; path=/";
    const calls = mockFetch({ body: "{}" });
    await api("/api/x", { method: "post", json: { a: 1 } });
    const { init } = calls[0];
    expect(init.method).toBe("POST");
    expect(init.body).toBe('{"a":1}');
    expect(init.headers.get("content-type")).toBe("application/json");
    expect(init.headers.get("x-csrf-token")).toBe("tok+1");
  });

  it("falls back to the last csrf_token returned by the API when the cookie is unreadable", async () => {
    const { api } = await load();
    const calls = mockFetch({ body: '{"csrf_token":"from-body"}' }, { body: "{}" });
    await api("/api/auth/login", { method: "POST", json: {} });
    await api("/api/me/delete", { method: "POST" });
    expect(calls[1].init.headers.get("x-csrf-token")).toBe("from-body");
  });

  it("sends an empty CSRF token when none is known", async () => {
    const { api } = await load();
    const calls = mockFetch({ body: "{}" });
    await api("/api/x", { method: "DELETE" });
    expect(calls[0].init.headers.get("x-csrf-token")).toBe("");
  });

  it("returns null for empty bodies", async () => {
    const { api } = await load();
    mockFetch({ status: 200, body: "" });
    expect(await api("/api/x")).toBeNull();
  });

  it("raises ApiError with the backend detail", async () => {
    const { api, ApiError } = await load();
    mockFetch({ status: 409, body: '{"detail":"An account with this email already exists"}' });
    const err = await api("/api/auth/register", { method: "POST" }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(409);
    expect(err.message).toBe("An account with this email already exists");
  });

  it("joins pydantic validation errors into one message", async () => {
    const { api } = await load();
    mockFetch({ status: 422, body: JSON.stringify({ detail: [
      { msg: "Value error, Enter a valid phone" }, { msg: "Field required" }] }) });
    await expect(api("/api/x", { method: "POST" })).rejects.toThrow("Enter a valid phone. Field required");
  });

  it("uses plain-text error bodies, or a generic message", async () => {
    const { api } = await load();
    mockFetch({ status: 502, body: "Bad Gateway" }, { status: 500, body: "" });
    await expect(api("/api/x")).rejects.toThrow("Bad Gateway");
    await expect(api("/api/x")).rejects.toThrow("Request failed (500)");
  });

  it("keeps caller headers", async () => {
    const { api } = await load();
    const calls = mockFetch({ body: "{}" });
    await api("/api/x", { headers: { "x-extra": "1" } });
    expect(calls[0].init.headers.get("x-extra")).toBe("1");
  });
});

describe("formatters", () => {
  it("mmss pads seconds and floors", async () => {
    const { mmss } = await load();
    expect(mmss(0)).toBe("0:00");
    expect(mmss(65.9)).toBe("1:05");
    expect(mmss(600)).toBe("10:00");
  });

  it("fmtDate handles empty values", async () => {
    const { fmtDate } = await load();
    expect(fmtDate(null)).toBe("—");
    expect(fmtDate(undefined)).toBe("—");
    expect(fmtDate("2026-10-29T10:00:00Z")).toMatch(/2026/);
  });

  it("has a label for every project status", async () => {
    const { STATUS_LABEL } = await load();
    for (const s of ["collecting_media", "awaiting_preferences", "awaiting_confirmation", "queued", "processing",
                     "completed", "failed", "cancelled"]) {
      expect(STATUS_LABEL[s]).toBeTruthy();
    }
  });
});
