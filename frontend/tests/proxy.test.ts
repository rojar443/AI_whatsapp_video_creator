// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DELETE, GET, POST } from "@/app/api/[...path]/route";

type Seen = { url: string; init: any };

function backend(res: Response | Error) {
  const seen: Seen[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: any) => {
    seen.push({ url, init });
    if (res instanceof Error) throw res;
    return res;
  }));
  return seen;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("/api proxy", () => {
  it("forwards path, query and headers to BACKEND_URL", async () => {
    vi.stubEnv("BACKEND_URL", "http://api:8100/");
    const seen = backend(new Response('{"ok":1}', { status: 201, headers: { "content-type": "application/json" } }));
    const req = new NextRequest("https://reel.example/api/projects?limit=5", {
      headers: { host: "reel.example", cookie: "rc_session=abc", "x-forwarded-for": "1.2.3.4", connection: "keep-alive" },
    });
    const res = await GET(req);
    expect(seen[0].url).toBe("http://api:8100/api/projects?limit=5");
    const h: Headers = seen[0].init.headers;
    expect(h.get("cookie")).toBe("rc_session=abc");
    expect(h.get("x-forwarded-for")).toBe("1.2.3.4");
    expect(h.get("x-forwarded-proto")).toBe("https");
    expect(h.get("x-forwarded-host")).toBe("reel.example");
    expect(h.has("host")).toBe(false);
    expect(h.has("connection")).toBe(false);
    expect(seen[0].init.body).toBeUndefined();
    expect(seen[0].init.redirect).toBe("manual");
    expect(res.status).toBe(201);
    expect(await res.json()).toEqual({ ok: 1 });
  });

  it("defaults to localhost and keeps an upstream forwarded proto", async () => {
    vi.stubEnv("BACKEND_URL", "");
    const seen = backend(new Response("{}"));
    await GET(new NextRequest("http://x/api/health", { headers: { "x-forwarded-proto": "https" } }));
    expect(seen[0].url).toBe("http://localhost:8100/api/health");
    expect(seen[0].init.headers.get("x-forwarded-proto")).toBe("https");
  });

  it("streams request bodies for writes", async () => {
    const seen = backend(new Response("{}"));
    await POST(new NextRequest("http://x/api/auth/login", { method: "POST", body: '{"a":1}',
                                                          headers: { "content-type": "application/json" } }));
    expect(seen[0].init.method).toBe("POST");
    expect(seen[0].init.body).toBeTruthy();
    expect(seen[0].init.duplex).toBe("half");
    await DELETE(new NextRequest("http://x/api/me/telegram", { method: "DELETE" }));
    expect(seen[1].init.method).toBe("DELETE");
  });

  it("strips hop-by-hop response headers but keeps cookies and redirects", async () => {
    backend(new Response(null, { status: 302, headers: {
      location: "https://s3.test/f", "set-cookie": "rc_csrf=1", "content-encoding": "gzip", connection: "close" } }));
    const res = await GET(new NextRequest("http://x/api/files/1"));
    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe("https://s3.test/f");
    expect(res.headers.get("set-cookie")).toBe("rc_csrf=1");
    expect(res.headers.has("content-encoding")).toBe(false);
  });

  it("returns 502 when the backend is down", async () => {
    backend(new TypeError("fetch failed"));
    const res = await GET(new NextRequest("http://x/api/me"));
    expect(res.status).toBe(502);
    expect(await res.json()).toEqual({ detail: "Backend unreachable" });
  });
});
