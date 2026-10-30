import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CONFIG, me, mockApi, mockLocation } from "./helpers";

const nav = vi.hoisted(() => ({ path: "/" }));
vi.mock("next/navigation", () => ({ usePathname: () => nav.path }));
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: any) => <a href={href} {...rest}>{children}</a>,
}));

import Nav from "@/components/Nav";
import RegisterLink from "@/components/RegisterLink";
import StartButton from "@/components/StartButton";
import TelegramCard from "@/components/TelegramCard";
import WhatsAppCard from "@/components/WhatsAppCard";
import { useMe } from "@/lib/useMe";

beforeEach(() => {
  nav.path = "/";
  mockLocation();
});
afterEach(() => vi.unstubAllGlobals());

// ------------------------------------------------------------------ StartButton / RegisterLink
describe("StartButton", () => {
  it("links to the Telegram bot when configured", async () => {
    mockApi({ "GET /api/config": CONFIG });
    render(<StartButton />);
    expect(await screen.findByText("Open in Telegram")).toHaveProperty("href", "https://t.me/reelbot");
  });

  it("falls back to 'How it works' without a bot (or while loading)", async () => {
    mockApi({ "GET /api/config": { ...CONFIG, telegram_bot: null } });
    render(<StartButton />);
    expect(screen.getByText("How it works").getAttribute("href")).toBe("#how");
  });
});

describe("RegisterLink", () => {
  it("shows only when registration is enabled", async () => {
    mockApi({ "GET /api/config": CONFIG });
    const { unmount } = render(<RegisterLink />);
    expect(await screen.findByText("Create account")).toBeTruthy();
    unmount();
    mockApi({ "GET /api/config": { ...CONFIG, registration_enabled: false } });
    const { container } = render(<RegisterLink />);
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });

  it("stays hidden when config fails to load", async () => {
    mockApi({ "GET /api/config": [500, { detail: "down" }] });
    const { container } = render(<RegisterLink />);
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });
});

// ------------------------------------------------------------------ Nav
describe("Nav", () => {
  it("shows public links for visitors", async () => {
    mockApi({ "GET /api/me": [401, { detail: "Not logged in" }], "GET /api/config": CONFIG });
    render(<Nav />);
    expect(await screen.findByText("Create account")).toBeTruthy();
    expect(screen.getByText("Log in")).toBeTruthy();
    expect(screen.getByText("Open in Telegram")).toBeTruthy();
    expect(screen.queryByText("Try the simulator")).toBeNull();
    expect(screen.queryByText("Videos")).toBeNull();
  });

  it("shows the simulator link in dev mode", async () => {
    mockApi({ "GET /api/me": [401, {}], "GET /api/config": { ...CONFIG, dev_mode: true } });
    render(<Nav />);
    expect(await screen.findByText("Try the simulator")).toBeTruthy();
  });

  it("shows account links, admin link and highlights the active page", async () => {
    nav.path = "/dashboard/projects/1";
    mockApi({ "GET /api/me": me({ role: "admin" }), "GET /api/config": { ...CONFIG, dev_mode: true } });
    render(<Nav />);
    expect(await screen.findByText("Admin")).toBeTruthy();
    expect(screen.getByText("Videos").className).toBe("active");
    expect(screen.getByText("Account").className).toBe("");
    expect(await screen.findByText("WhatsApp simulator")).toBeTruthy();
  });

  it("hides the admin link for normal users", async () => {
    mockApi({ "GET /api/me": me(), "GET /api/config": CONFIG });
    render(<Nav />);
    await screen.findByText("Videos");
    expect(screen.queryByText("Admin")).toBeNull();
  });

  it("logs out and goes home even if the request fails", async () => {
    const loc = mockLocation();
    const calls = mockApi({ "GET /api/me": me(), "GET /api/config": CONFIG, "POST /api/auth/logout": [500, {}] });
    render(<Nav />);
    await userEvent.click(await screen.findByText("Log out"));
    await waitFor(() => expect(loc.href).toBe("/"));
    expect(calls.some((c) => c.method === "POST" && c.url === "/api/auth/logout")).toBe(true);
  });
});

// ------------------------------------------------------------------ TelegramCard
describe("TelegramCard", () => {
  it("renders nothing when Telegram is disabled", () => {
    const { container } = render(<TelegramCard me={me({ telegram: { enabled: false } }) as any} onChange={() => {}} />);
    expect(container.textContent).toBe("");
  });

  it("shows the deep link after connecting", async () => {
    mockApi({ "POST /api/me/telegram/link": { status: "pending", url: "https://t.me/reelbot?start=link_x" } });
    const onChange = vi.fn();
    render(<TelegramCard me={me() as any} onChange={onChange} />);
    expect(screen.getByText("Not connected")).toBeTruthy();
    await userEvent.click(screen.getByText("Connect Telegram"));
    expect((await screen.findByText("Open Telegram")).getAttribute("href")).toBe("https://t.me/reelbot?start=link_x");
    await userEvent.click(screen.getByText("I've done it"));
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("refreshes when already linked on the server", async () => {
    mockApi({ "POST /api/me/telegram/link": { status: "linked" } });
    const onChange = vi.fn();
    render(<TelegramCard me={me() as any} onChange={onChange} />);
    await userEvent.click(screen.getByText("Connect Telegram"));
    await waitFor(() => expect(onChange).toHaveBeenCalled());
  });

  it("shows errors", async () => {
    mockApi({ "POST /api/me/telegram/link": [503, { detail: "Telegram is not configured on this server" }] });
    render(<TelegramCard me={me() as any} onChange={() => {}} />);
    await userEvent.click(screen.getByText("Connect Telegram"));
    expect(await screen.findByText("Telegram is not configured on this server")).toBeTruthy();
  });

  it("disconnects", async () => {
    const calls = mockApi({ "DELETE /api/me/telegram": { ok: true } });
    const onChange = vi.fn();
    render(<TelegramCard me={me({ telegram: { enabled: true, linked: true } }) as any} onChange={onChange} />);
    expect(screen.getByText("Connected")).toBeTruthy();
    await userEvent.click(screen.getByText("Disconnect"));
    await waitFor(() => expect(onChange).toHaveBeenCalled());
    expect(calls[0].method).toBe("DELETE");
  });
});

// ------------------------------------------------------------------ WhatsAppCard
const wa = (over: Record<string, any>) => ({ enabled: true, status: "not_linked", number: null, linked_at: null,
                                              business_number: "15550000000", ...over });

describe("WhatsAppCard", () => {
  it("hidden when WhatsApp is disabled or there is no phone", () => {
    const a = render(<WhatsAppCard me={me({ whatsapp: wa({ enabled: false }) }) as any} onChange={() => {}} />);
    expect(a.container.textContent).toBe("");
    a.unmount();
    const b = render(<WhatsAppCard me={me({ phone: null }) as any} onChange={() => {}} />);
    expect(b.container.textContent).toBe("");
  });

  it("shows the connected state even without a phone", () => {
    render(<WhatsAppCard me={me({ phone: null, whatsapp: wa({ status: "linked", number: "+1 555" }) }) as any}
                         onChange={() => {}} />);
    expect(screen.getByText("Connected")).toBeTruthy();
    expect(screen.getByText("Open chat").getAttribute("href")).toBe("https://wa.me/15550000000");
  });

  it("issues a link code", async () => {
    mockApi({ "POST /api/me/whatsapp/link": { status: "pending", code: "123456", message: "LINK 123456",
                                              wa_me_url: "https://wa.me/1?text=LINK%20123456",
                                              expires_in_minutes: 15, send_from: "+15557654321" } });
    render(<WhatsAppCard me={me({ whatsapp: wa({ status: "pending" }) }) as any} onChange={() => {}} />);
    expect(screen.getByText("Pending")).toBeTruthy();
    await userEvent.click(screen.getByText("Get link code"));
    expect(await screen.findByText("LINK 123456")).toBeTruthy();
    expect(screen.getByText("Code expires in 15 minutes.")).toBeTruthy();
    expect(screen.getByText("Open WhatsApp").getAttribute("href")).toContain("LINK%20123456");
  });

  it("refreshes when already linked, and shows errors", async () => {
    const onChange = vi.fn();
    mockApi({ "POST /api/me/whatsapp/link": { status: "linked" } });
    const r = render(<WhatsAppCard me={me() as any} onChange={onChange} />);
    await userEvent.click(screen.getByText("Get link code"));
    await waitFor(() => expect(onChange).toHaveBeenCalled());
    r.unmount();
    mockApi({ "POST /api/me/whatsapp/link": [409, { detail: "This WhatsApp number is linked to another account" }] });
    render(<WhatsAppCard me={me() as any} onChange={onChange} />);
    await userEvent.click(screen.getByText("Get link code"));
    expect(await screen.findByText("This WhatsApp number is linked to another account")).toBeTruthy();
  });
});

// ------------------------------------------------------------------ useMe
describe("useMe", () => {
  it("loads the current user", async () => {
    mockApi({ "GET /api/me": me() });
    const { result } = renderHook(() => useMe());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.me?.name).toBe("Asha");
  });

  it("redirects to login when required and logged out", async () => {
    const loc = mockLocation("", "/account");
    mockApi({ "GET /api/me": [401, { detail: "Not logged in" }] });
    const { result } = renderHook(() => useMe({ required: true }));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.me).toBeNull();
    expect(loc.href).toBe("/login?next=%2Faccount");
  });

  it("does not redirect on other errors or when optional", async () => {
    const loc = mockLocation("", "/account");
    mockApi({ "GET /api/me": [500, {}] });
    const a = renderHook(() => useMe({ required: true }));
    await waitFor(() => expect(a.result.current.loading).toBe(false));
    mockApi({ "GET /api/me": [401, {}] });
    const b = renderHook(() => useMe());
    await waitFor(() => expect(b.result.current.loading).toBe(false));
    expect(loc.href).toBe("");
  });

  it("refresh() reloads", async () => {
    let n = 0;
    mockApi({ "GET /api/me": () => me({ credits: ++n }) });
    const { result } = renderHook(() => useMe());
    await waitFor(() => expect(result.current.me?.credits).toBe(1));
    await act(() => result.current.refresh());
    expect(result.current.me?.credits).toBe(2);
  });
});
