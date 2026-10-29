import { vi } from "vitest";

type Reply = unknown | [number, unknown];
export type Routes = Record<string, Reply | ((body: any, url: string) => Reply)>;

/** Route-table fetch mock: keys are "METHOD /path" (query string ignored unless the key includes one). */
export function mockApi(routes: Routes) {
  const calls: { method: string; url: string; body: any }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = (init.method || "GET").toUpperCase();
    const body = typeof init.body === "string" ? JSON.parse(init.body) : init.body;
    calls.push({ method, url, body });
    const key = [`${method} ${url}`, `${method} ${url.split("?")[0]}`].find((k) => k in routes);
    if (!key) return new Response(JSON.stringify({ detail: `unmocked ${method} ${url}` }), { status: 599 });
    let r = routes[key];
    if (typeof r === "function") r = (r as (b: any, u: string) => Reply)(body, url);
    const [status, data] = Array.isArray(r) && typeof r[0] === "number" ? (r as [number, unknown]) : [200, r];
    return new Response(data === undefined ? "" : JSON.stringify(data), { status });
  }));
  return calls;
}

/** Replace window.location so pages can assign href / read search without jsdom navigation errors. */
export function mockLocation(search = "", pathname = "/") {
  const loc = { href: "", search, pathname, origin: "http://localhost" };
  Object.defineProperty(window, "location", { value: loc, writable: true, configurable: true });
  return loc;
}

export const CONFIG = { dev_mode: false, registration_enabled: true, whatsapp_enabled: true, telegram_bot: "reelbot" };

export function me(over: Record<string, any> = {}) {
  return {
    id: "u1", name: "Asha", email: "asha@x.test", phone: "+15557654321", role: "user", is_anonymous: false,
    email_verified: true, credits: 10,
    telegram: { enabled: true, linked: false, linked_at: null },
    whatsapp: { enabled: true, status: "not_linked", number: null, linked_at: null, business_number: "15550000000" },
    ...over,
  };
}
