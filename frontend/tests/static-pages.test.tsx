import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CONFIG, mockApi, mockLocation } from "./helpers";

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: any) => <a href={href} {...rest}>{children}</a>,
}));
vi.mock("next/navigation", () => ({ usePathname: () => "/" }));
vi.mock("next/font/google", () => ({
  Bricolage_Grotesque: () => ({ variable: "font-display" }),
  Plus_Jakarta_Sans: () => ({ variable: "font-body" }),
}));

import Home from "@/app/page";
import RootLayout, { metadata as layoutMeta, viewport } from "@/app/layout";
import Privacy, { metadata as privacyMeta } from "@/app/privacy/page";
import Terms from "@/app/terms/page";
import { useConfig } from "@/lib/useConfig";

beforeEach(() => mockLocation());
afterEach(() => vi.unstubAllGlobals());

// ------------------------------------------------------------------ useConfig
describe("useConfig", () => {
  it("starts null, then loads the public config once", async () => {
    const calls = mockApi({ "GET /api/config": CONFIG });
    const { result, rerender } = renderHook(() => useConfig());
    expect(result.current).toBeNull();
    await waitFor(() => expect(result.current).toEqual(CONFIG));
    rerender();
    expect(calls.filter((c) => c.url === "/api/config")).toHaveLength(1);
  });

  it("stays null and does not throw when the request fails", async () => {
    mockApi({ "GET /api/config": [500, { detail: "down" }] });
    const { result } = renderHook(() => useConfig());
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    await act(async () => {});
    expect(result.current).toBeNull();
  });

  it("stays null when the network is down", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("fetch failed"); }));
    const { result } = renderHook(() => useConfig());
    await act(async () => {});
    expect(result.current).toBeNull();
  });
});

// ------------------------------------------------------------------ Home
describe("Home", () => {
  it("shows the hero, the CTAs and the demo chat", async () => {
    mockApi({ "GET /api/config": CONFIG });
    render(<Home />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toContain("Reels");
    expect((await screen.findByText("Open in Telegram")).getAttribute("href")).toBe("https://t.me/reelbot");
    expect(screen.getByText("Log in").getAttribute("href")).toBe("/login");
    expect(await screen.findByText("Create account")).toBeTruthy();
    const chat = screen.getByLabelText("Example Telegram conversation");
    expect(chat.querySelectorAll(".bubble.out")).toHaveLength(4);
    expect(chat.querySelectorAll(".bubble.in")).toHaveLength(3);
  });

  it("explains how it works with four steps and six feature cards", () => {
    mockApi({ "GET /api/config": CONFIG });
    const { container } = render(<Home />);
    expect(container.querySelector("#how")).toBeTruthy();
    expect(container.querySelectorAll("ol.steps li")).toHaveLength(4);
    expect(screen.getAllByRole("heading", { level: 3 })).toHaveLength(6);
    expect(screen.getByText("Fair credits")).toBeTruthy();
  });

  it("falls back to the anchor link and hides registration when disabled", async () => {
    mockApi({ "GET /api/config": { ...CONFIG, telegram_bot: null, registration_enabled: false } });
    render(<Home />);
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    await act(async () => {});
    expect(screen.getByRole("link", { name: "How it works" }).getAttribute("href")).toBe("#how");
    expect(screen.queryByText("Create account")).toBeNull();
  });
});

// ------------------------------------------------------------------ Layout
describe("RootLayout", () => {
  it("wraps pages with nav, main content and legal footer links", () => {
    const html = renderToStaticMarkup(<RootLayout><p>child-content</p></RootLayout>);
    expect(html).toMatch(/^<html lang="en" class="font-display font-body">/);
    expect(html).toContain("<nav");
    expect(html).toMatch(/<main><div class="container"><p>child-content<\/p><\/div><\/main>/);
    expect(html).toContain('href="/privacy"');
    expect(html).toContain('href="/terms"');
  });

  it("exports metadata and a mobile viewport", () => {
    expect(layoutMeta.title).toBe("ReelChat");
    expect(String(layoutMeta.description)).toContain("Telegram");
    expect(viewport).toEqual({ width: "device-width", initialScale: 1 });
  });
});

// ------------------------------------------------------------------ Legal pages
describe("Legal pages", () => {
  it("privacy policy covers retention and deletion", () => {
    render(<Privacy />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Privacy Policy");
    for (const h of ["What we collect", "Retention", "Your rights", "Security"]) {
      expect(screen.getByRole("heading", { name: h })).toBeTruthy();
    }
    expect(screen.getByText(/deleted 7 days after upload/)).toBeTruthy();
    expect(privacyMeta.title).toContain("Privacy");
  });

  it("terms page renders", () => {
    render(<Terms />);
    expect(screen.getByRole("heading", { level: 1 })).toBeTruthy();
  });
});
