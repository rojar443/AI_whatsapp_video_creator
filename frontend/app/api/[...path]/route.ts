import { NextRequest } from "next/server";

// Runtime reverse proxy to the FastAPI backend (BACKEND_URL is read per request, not baked in at build time).
// Keeps cookies first-party on the website's domain, so the session and CSRF cookies just work on Render.
export const dynamic = "force-dynamic";

const HOP = ["host", "connection", "content-length", "transfer-encoding", "keep-alive", "upgrade", "te", "trailer"];

async function proxy(req: NextRequest): Promise<Response> {
  const backend = (process.env.BACKEND_URL ||
    (process.env.BACKEND_HOSTPORT ? `http://${process.env.BACKEND_HOSTPORT}` : "http://localhost:8100")
  ).replace(/\/$/, "");
  const url = backend + req.nextUrl.pathname + req.nextUrl.search;
  const headers = new Headers(req.headers);
  HOP.forEach((h) => headers.delete(h));
  const fwd = req.headers.get("x-forwarded-for");
  if (fwd) headers.set("x-forwarded-for", fwd);
  headers.set("x-forwarded-proto", req.headers.get("x-forwarded-proto") || req.nextUrl.protocol.replace(":", ""));
  headers.set("x-forwarded-host", req.headers.get("host") || "");
  const hasBody = !["GET", "HEAD"].includes(req.method);
  let res: Response;
  try {
    res = await fetch(url, { method: req.method, headers, body: hasBody ? req.body : undefined,
                             // @ts-expect-error Node fetch needs duplex for streamed bodies
                             duplex: "half", redirect: "manual" });
  } catch {
    return Response.json({ detail: "Backend unreachable" }, { status: 502 });
  }
  const out = new Headers(res.headers);
  ["content-encoding", "content-length", "transfer-encoding", "connection"].forEach((h) => out.delete(h));
  return new Response(res.body, { status: res.status, headers: out });
}

export { proxy as GET, proxy as POST, proxy as PUT, proxy as PATCH, proxy as DELETE };
