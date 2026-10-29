import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CONFIG, me, mockApi, mockLocation } from "./helpers";

const params = vi.hoisted(() => ({ id: "p1" }));
vi.mock("next/navigation", () => ({ useParams: () => params, usePathname: () => "/" }));
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: any) => <a href={href} {...rest}>{children}</a>,
}));

import Account from "@/app/account/page";
import Admin from "@/app/admin/page";
import Dashboard from "@/app/dashboard/page";
import ProjectPage from "@/app/dashboard/projects/[id]/page";
import Simulator from "@/app/dev/whatsapp/page";

let loc: ReturnType<typeof mockLocation>;
beforeEach(() => {
  loc = mockLocation("", "/dashboard");
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const video = (over: Record<string, any> = {}) => ({
  id: "a1", kind: "video", url: "http://x/api/files/a1?exp=1&sig=s", width: 1080, height: 1920, duration_seconds: 29.6,
  is_demo: false, demo: null, title: "Goa", created_at: "2026-10-29T10:00:00Z", expires_at: null, size_bytes: 4_200_000,
  job_id: "j1", deleted: false, ...over,
});

const summary = (over: Record<string, any> = {}) => ({
  id: "p1", title: "Goa days", status: "completed", stage: null, revision: 2, last_error: null, sources: 4,
  created_at: "2026-10-29T10:00:00Z", updated_at: "2026-10-29T10:00:00Z", video: video(), ...over,
});

// ------------------------------------------------------------------ dashboard
describe("Dashboard", () => {
  it("lists reels with status, errors and demo badges", async () => {
    mockApi({
      "GET /api/me": me({ name: "Asha Nair", credits: 7 }),
      "GET /api/projects": { items: [
        summary(),
        summary({ id: "p2", title: "Broken", status: "failed", last_error: "render failed", video: null }),
        summary({ id: "p3", title: "Demo", status: "mystery_status", video: video({ is_demo: true }) }),
      ] },
    });
    render(<Dashboard />);
    expect(await screen.findByText("Hi Asha")).toBeTruthy();
    expect(screen.getByText("7 credits")).toBeTruthy();
    const card = (await screen.findByText("Goa days")).closest("a")!;
    expect(card.getAttribute("href")).toBe("/dashboard/projects/p1");
    expect(card.querySelector("video")!.getAttribute("src")).toBe("http://x/api/files/a1?exp=1&sig=s&inline=1");
    expect(within(card).getByText(/4 uploads · revision 2/)).toBeTruthy();
    expect(screen.getByText("render failed")).toBeTruthy();
    expect(screen.getAllByText("Failed").length).toBe(2); // placeholder + badge
    expect(screen.getByText("Demo output")).toBeTruthy();
    expect(screen.getAllByText("mystery_status").length).toBe(1);
  });

  it("shows an empty state", async () => {
    mockApi({ "GET /api/me": me(), "GET /api/projects": { items: [] } });
    render(<Dashboard />);
    expect(await screen.findByText(/No reels yet/)).toBeTruthy();
  });

  it("refreshes the list every 5 s", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const calls = mockApi({ "GET /api/me": me(), "GET /api/projects": { items: [] } });
    render(<Dashboard />);
    await screen.findByText(/No reels yet/);
    const n = calls.filter((c) => c.url === "/api/projects").length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(calls.filter((c) => c.url === "/api/projects").length).toBe(n + 1);
  });

  it("redirects logged-out visitors to login", async () => {
    mockApi({ "GET /api/me": [401, {}] });
    render(<Dashboard />);
    await waitFor(() => expect(loc.href).toBe("/login?next=%2Fdashboard"));
    expect(screen.getByText("Loading…")).toBeTruthy();
  });
});

// ------------------------------------------------------------------ account
describe("Account", () => {
  const usage = { credits: 9, max_jobs_per_day: 20, ledger: [
    { kind: "credit_grant", credits: 10, at: "2026-10-29T10:00:00Z" },
    { kind: "credit_reserve", credits: -2, at: "2026-10-29T11:00:00Z" },
    { kind: "something_new", credits: 1, at: "2026-10-29T12:00:00Z" },
  ] };

  it("shows profile and the credit ledger", async () => {
    mockApi({ "GET /api/me": me({ email_verified: false, phone: null }), "GET /api/me/usage": usage });
    render(<Account />);
    expect(await screen.findByText("unverified")).toBeTruthy();
    expect(await screen.findByText("Sign-up bonus")).toBeTruthy();
    expect(screen.getByText("+10")).toBeTruthy();
    expect(screen.getByText("-2")).toBeTruthy();
    expect(screen.getByText("something_new")).toBeTruthy();
    expect(screen.getByText(/Limit: 20 videos\/day/)).toBeTruthy();
    expect(screen.queryByText("Unlink WhatsApp")).toBeNull();
  });

  it("unlinks WhatsApp only after confirmation", async () => {
    const calls = mockApi({ "GET /api/me": me({ whatsapp: { ...me().whatsapp, status: "linked" } }),
                            "GET /api/me/usage": usage, "DELETE /api/me/whatsapp": { ok: true } });
    const confirm = vi.fn(() => false);
    vi.stubGlobal("confirm", confirm);
    render(<Account />);
    await userEvent.click(await screen.findByText("Unlink WhatsApp"));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    confirm.mockReturnValue(true);
    await userEvent.click(screen.getByText("Unlink WhatsApp"));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
  });

  it("deletes the account after confirmation", async () => {
    const calls = mockApi({ "GET /api/me": me(), "GET /api/me/usage": usage, "POST /api/me/delete": { ok: true } });
    vi.stubGlobal("confirm", vi.fn(() => true));
    render(<Account />);
    await userEvent.type(await screen.findByLabelText(/Delete account/), "secret");
    await userEvent.click(screen.getByText("Delete everything"));
    await waitFor(() => expect(loc.href).toBe("/"));
    expect(calls.find((c) => c.url === "/api/me/delete")!.body).toEqual({ password: "secret" });
  });

  it("shows a wrong-password error and does nothing when cancelled", async () => {
    const calls = mockApi({ "GET /api/me": me(), "GET /api/me/usage": usage,
                            "POST /api/me/delete": [403, { detail: "Password is incorrect" }] });
    const confirm = vi.fn(() => false);
    vi.stubGlobal("confirm", confirm);
    render(<Account />);
    await userEvent.type(await screen.findByLabelText(/Delete account/), "x");
    await userEvent.click(screen.getByText("Delete everything"));
    expect(calls.some((c) => c.url === "/api/me/delete")).toBe(false);
    confirm.mockReturnValue(true);
    await userEvent.click(screen.getByText("Delete everything"));
    expect(await screen.findByText("Password is incorrect")).toBeTruthy();
    expect(loc.href).toBe("");
  });
});

// ------------------------------------------------------------------ project detail
function detail(over: Record<string, any> = {}) {
  return {
    ...summary(),
    preferences: [{ revision: 1, values: {}, sources: {}, playbook: null, plan: null }, {
      revision: 2,
      values: { genre: "daily_life", captions: false, music: "licensed", vibe: "", duration: null, title_text: "Goa" },
      sources: { genre: "user", music: "default" }, playbook: "travel@3",
      plan: {
        items: [
          { segment_id: "s1", asset_id: "x1", kind: "video", source_start: 3, source_end: 65, role: "hook", reason: "",
            description: "sunset over the sea" },
          { segment_id: "s2", asset_id: "x2", kind: "photo", source_start: 0, source_end: 0, role: "build",
            reason: "nice light", description: "" },
          { segment_id: "s3", asset_id: "zz", kind: "video", source_start: 0, source_end: 2, role: "payoff", reason: "",
            description: "" },
        ],
        total_seconds: 29.6, dropped: [{ asset_id: "x9", label: "Clip 9", reason: "too dark" }], notes: [],
        script: { title: "T", lines: [{ item_index: 0, text: "Golden hour." }, { item_index: 2, text: "Home." }] },
        music_title: "Calm bed", scorer: "gemini-vision", pipeline: {}, labels: { x1: "Clip 1", x2: "Photo 1" },
      },
    }],
    jobs: [{ id: "j1", revision: 2, status: "failed", stage: "render", attempts: 1, run_number: 1, credits_charged: 0,
             pipeline: { render: "builtin" }, error_code: "render_failed", error_message: "ffmpeg died",
             created_at: "2026-10-29T10:00:00Z", finished_at: null }],
    assets: [video({ id: "old", deleted: true, url: null }), video(), { ...video({ id: "srt" }), kind: "subtitles" }],
    source_files: [
      { id: "f1", label: "Clip 1", kind: "video", status: "ready", reject_reason: null, duration_seconds: 12.34 },
      { id: "f2", label: "Clip 9", kind: "video", status: "rejected", reject_reason: "too dark", duration_seconds: null },
      { id: "f3", label: "Photo 1", kind: "photo", status: "pending", reject_reason: null, duration_seconds: null },
    ],
    can_retry: true,
    ...over,
  };
}

describe("Project page", () => {
  it("renders the plan, settings, uploads and history", async () => {
    mockApi({ "GET /api/me": me(), "GET /api/projects/p1": detail({ status: "failed", last_error: "render failed" }) });
    render(<ProjectPage />);
    expect(await screen.findByText("Goa days")).toBeTruthy();
    expect(screen.getByText(/Editing failed: render failed/)).toBeTruthy();
    expect(screen.getByText("Settings · revision 2")).toBeTruthy();
    expect(screen.getByText("daily life")).toBeTruthy(); // underscores replaced
    expect(screen.getByText("Off")).toBeTruthy(); // boolean captions
    expect(screen.getAllByText(/· suggested/).length).toBe(1);
    expect(screen.queryByText("Vibe")).toBeNull(); // empty values hidden
    expect(screen.getByText("Playbook: travel@3")).toBeTruthy();
    expect(screen.getByText("Plan · ~30 s")).toBeTruthy();
    const items = screen.getByText("Plan · ~30 s").parentElement!.querySelectorAll("li");
    expect(items[0].textContent).toBe("Opening — Clip 1 0:03–1:05: sunset over the sea");
    expect(items[1].textContent).toBe("Photo 1: nice light");
    expect(items[2].textContent).toBe("Ending — clip 0:00–0:02");
    expect(screen.getByText(/Skipped: Clip 9 \(too dark\)/)).toBeTruthy();
    expect(screen.getByText(/Golden hour\. Home\./)).toBeTruthy();
    expect(screen.getByText("Moment scoring: gemini-vision · Music: Calm bed")).toBeTruthy();
    expect(screen.getByText("Expired")).toBeTruthy();
    expect(screen.getAllByText("1080×1920 · 30 s · 4.2 MB", { exact: false }).length).toBe(2);
    expect(screen.getByText("Download").getAttribute("href")).toBe(video().url);
    expect(screen.getByText("Your uploads (3)")).toBeTruthy();
    expect(screen.getByText(/Clip 1 · 12\.3 s/)).toBeTruthy();
    expect(screen.getByText("ffmpeg died")).toBeTruthy();
  });

  it("retries a failed project", async () => {
    const calls = mockApi({ "GET /api/me": me(), "GET /api/projects/p1": detail({ status: "failed" }),
                            "POST /api/projects/p1/retry": { ok: true, job_id: "j2" } });
    render(<ProjectPage />);
    await userEvent.click(await screen.findByText("Retry"));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/projects/p1/retry")).toBe(true));
  });

  it("shows retry errors without losing the page", async () => {
    mockApi({ "GET /api/me": me(), "GET /api/projects/p1": detail({ status: "failed" }),
              "POST /api/projects/p1/retry": [402, { detail: "Not enough credits" }] });
    render(<ProjectPage />);
    await userEvent.click(await screen.findByText("Retry"));
    expect(await screen.findByText("Not enough credits")).toBeTruthy();
    expect(screen.getByText("Goa days")).toBeTruthy();
  });

  it("hides retry when not retryable, and handles early states", async () => {
    mockApi({ "GET /api/me": me(), "GET /api/projects/p1": detail({
      status: "collecting_media", can_retry: false, preferences: [], jobs: [], assets: [], source_files: [] }) });
    render(<ProjectPage />);
    expect(await screen.findByText("Receiving clips")).toBeTruthy();
    expect(screen.queryByText("Retry")).toBeNull();
    expect(screen.getByText("No reel yet — continue in WhatsApp.")).toBeTruthy();
    expect(screen.getByText("Still collecting details on WhatsApp.")).toBeTruthy();
    expect(screen.getByText("Settings · revision 1")).toBeTruthy();
    expect(screen.getByText("Not started yet.")).toBeTruthy();
  });

  it("shows a not-found error", async () => {
    mockApi({ "GET /api/me": me(), "GET /api/projects/p1": [404, { detail: "Project not found" }] });
    render(<ProjectPage />);
    expect(await screen.findByText("Project not found")).toBeTruthy();
  });
});

// ------------------------------------------------------------------ admin
const row = (over: Record<string, any> = {}) => ({
  id: "u2", name: "Bea", email: null, channels: [{ kind: "telegram", id: "@bea", name: "Bea", status: "linked" }],
  registered: false, role: "user", status: "active", credits: 15, projects: 3, reels: 2, messages: 40, tokens: 12345,
  cost_usd: 0.004, first_seen: "2026-10-01T00:00:00Z", last_seen: "2026-10-29T00:00:00Z", ...over,
});

function adminRoutes(over: Record<string, any> = {}) {
  return {
    "GET /api/me": me({ id: "u1", role: "admin" }),
    "GET /api/admin/summary": { days: 30, users: 50, talked_to_bot: 42, tokens: 99999, cost_usd: 1.5,
                                by_provider: [{ provider: "gemini", kind: "llm", tokens: 99999, cost_usd: 1.5 },
                                              { provider: "x", kind: "llm", tokens: 0, cost_usd: 0 }] },
    "GET /api/admin/users": { items: [row(), row({ id: "u1", name: "Me", role: "admin", email: "me@x.test",
                                                   registered: true, credits: 5, cost_usd: 2.5,
                                                   channels: [{ kind: "web", id: null, name: null, status: "" }] })],
                              total: 60, limit: 25, offset: 0 },
    "PATCH /api/admin/users/u2": { ok: true },
    ...over,
  };
}

const myRowChannel = () => within(screen.getByText("Me").closest("tr")!).getByText("Website").textContent;

describe("Admin", () => {
  it("is admin-only", async () => {
    mockApi({ "GET /api/me": me() });
    render(<Admin />);
    expect(await screen.findByText("This area is for admins only.")).toBeTruthy();
  });

  it("shows summary cards and the user table", async () => {
    mockApi(adminRoutes());
    render(<Admin />);
    expect(await screen.findByText("42")).toBeTruthy();
    expect(screen.getByText("of 50 people in total")).toBeTruthy();
    expect(screen.getByText("$1.50")).toBeTruthy();
    expect(screen.getByText(`gemini: ${(99999).toLocaleString()}`)).toBeTruthy();
    expect(await screen.findByText("Bea")).toBeTruthy();
    expect(screen.getByText("bot only")).toBeTruthy();
    expect(screen.getByText("<$0.01")).toBeTruthy();
    expect(myRowChannel()).toBe("Website");
    expect(screen.getByText("1–25 of 60")).toBeTruthy();
    const myRow = screen.getByText("Me").closest("tr")!;
    expect(within(myRow).queryByText("Suspend")).toBeNull(); // can't suspend yourself
    expect(within(myRow).getByText("−10")).toHaveProperty("disabled", true); // < 10 credits
  });

  it("adjusts credits and suspends users", async () => {
    const calls = mockApi(adminRoutes());
    render(<Admin />);
    const bea = (await screen.findByText("Bea")).closest("tr")!;
    await userEvent.click(within(bea).getByText("+10"));
    await userEvent.click(within(bea).getByText("Suspend"));
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH").length).toBe(2));
    const patches = calls.filter((c) => c.method === "PATCH").map((c) => c.body);
    expect(patches).toEqual([{ credits_delta: 10 }, { status: "suspended" }]);
  });

  it("reactivates suspended users and shows patch errors", async () => {
    mockApi(adminRoutes({
      "GET /api/admin/users": { items: [row({ status: "suspended" })], total: 1, limit: 25, offset: 0 },
      "PATCH /api/admin/users/u2": [400, { detail: "Not enough credits to remove" }],
    }));
    render(<Admin />);
    const bea = (await screen.findByText("Bea")).closest("tr")!;
    await userEvent.click(within(bea).getByText("Reactivate"));
    expect(await screen.findByText("Not enough credits to remove")).toBeTruthy();
  });

  it("builds the query from filters and paginates", async () => {
    const calls = mockApi(adminRoutes());
    render(<Admin />);
    await screen.findByText("Bea");
    fireEvent.change(screen.getByLabelText("Search users"), { target: { value: " bea " } });
    fireEvent.change(screen.getByLabelText("Channel"), { target: { value: "telegram" } });
    fireEvent.click(screen.getByRole("checkbox"));
    await waitFor(() => {
      const last = calls.filter((c) => c.url.startsWith("/api/admin/users?")).pop()!.url;
      const qs = new URLSearchParams(last.split("?")[1]);
      expect(qs.get("q")).toBe("bea");
      expect(qs.get("channel")).toBe("telegram");
      expect(qs.has("talked")).toBe(false);
    });
    fireEvent.click(screen.getByText("Next"));
    await waitFor(() => {
      const last = calls.filter((c) => c.url.startsWith("/api/admin/users?")).pop()!.url;
      expect(new URLSearchParams(last.split("?")[1]).get("offset")).toBe("25");
    });
    expect(screen.getByText("Previous")).toHaveProperty("disabled", false);
  });

  it("shows an empty-result hint and list errors", async () => {
    mockApi(adminRoutes({ "GET /api/admin/users": { items: [], total: 0, limit: 25, offset: 0 },
                          "GET /api/admin/summary": { days: 30, users: 0, talked_to_bot: 0, tokens: 0, cost_usd: 0,
                                                      by_provider: [] } }));
    render(<Admin />);
    expect(await screen.findByText(/No one matches\. Untick the box/)).toBeTruthy();
    expect(screen.getByText("0 of 0")).toBeTruthy();
    expect(screen.getByText("no usage yet")).toBeTruthy();
    expect(screen.getByText("$0.00")).toBeTruthy();
  });

  it("surfaces list errors", async () => {
    mockApi(adminRoutes({ "GET /api/admin/users": [500, { detail: "db down" }] }));
    render(<Admin />);
    expect(await screen.findByText("db down")).toBeTruthy();
  });
});

// ------------------------------------------------------------------ dev simulator
const msgs = [
  { id: "1", direction: "inbound", kind: "video", text: null, transcript: null, status: "processed", error: null,
    at: "2026-10-29T10:00:00Z" },
  { id: "2", direction: "inbound", kind: "audio", text: "ignored", transcript: "make it calm", status: "processed",
    error: null, at: "2026-10-29T10:00:01Z" },
  { id: "3", direction: "outbound", kind: "interactive", text: "Here's the plan", transcript: null, status: "sent",
    error: null, at: "2026-10-29T10:00:02Z", buttons: [{ id: "confirm:p:n", title: "✅ Proceed" }] },
  { id: "4", direction: "outbound", kind: "video", text: "Your reel", transcript: null, status: "failed",
    error: "131047", at: "2026-10-29T10:00:03Z", video_url: "http://x/v.mp4", via_template: true },
  { id: "5", direction: "inbound", kind: "image", text: null, transcript: null, status: "processed", error: null,
    at: "2026-10-29T10:00:04Z" },
];

describe("WhatsApp simulator", () => {
  it("is hidden outside development", async () => {
    mockApi({ "GET /api/me": [401, {}], "GET /api/dev/whatsapp/messages": [404, { detail: "Not found" }] });
    render(<Simulator />);
    expect(await screen.findByText("The simulator is only available in development mode.")).toBeTruthy();
  });

  it("renders the conversation", async () => {
    mockApi({ "GET /api/me": [401, {}], "GET /api/dev/whatsapp/messages": { items: msgs } });
    render(<Simulator />);
    expect(await screen.findByText("🎞 Video clip")).toBeTruthy();
    expect(screen.getByText('🎤 Voice note: "make it calm"')).toBeTruthy();
    expect(screen.queryByText("ignored")).toBeNull();
    expect(screen.getByText("📷 Photo")).toBeTruthy();
    expect(screen.getByText(/template · failed · 131047/)).toBeTruthy();
    expect(document.querySelector("video")!.getAttribute("src")).toBe("http://x/v.mp4");
  });

  it("sends text as the test number and ignores blank input", async () => {
    const calls = mockApi({ "GET /api/me": [401, {}], "GET /api/dev/whatsapp/messages": { items: [] },
                            "POST /api/dev/whatsapp/send": { accepted: 1 } });
    render(<Simulator />);
    expect(await screen.findByText(/Send some clips to start/)).toBeTruthy();
    const user = userEvent.setup();
    await user.click(screen.getByText("Send"));
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    await user.clear(screen.getByLabelText("Test phone number"));
    await user.type(screen.getByLabelText("Test phone number"), "+15550001111");
    await user.type(screen.getByLabelText("Message"), "hello");
    await user.click(screen.getByText("Send"));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ text: "hello", phone: "+15550001111" });
    expect(calls.filter((c) => c.url.startsWith("/api/dev/whatsapp/messages")).pop()!.url)
      .toBe("/api/dev/whatsapp/messages?phone=%2B15550001111");
    expect(screen.getByLabelText("Message")).toHaveProperty("value", "");
  });

  it("logged-in users send as their own number and can press buttons", async () => {
    const calls = mockApi({ "GET /api/me": me({ phone: "+15557654321" }),
                            "GET /api/dev/whatsapp/messages": { items: msgs },
                            "POST /api/dev/whatsapp/send": { accepted: 1 } });
    render(<Simulator />);
    expect(await screen.findByText("+15557654321")).toBeTruthy();
    expect(screen.queryByLabelText("Test phone number")).toBeNull();
    await userEvent.click(await screen.findByText("✅ Proceed"));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ button_id: "confirm:p:n", button_title: "✅ Proceed" });
    expect(calls.find((c) => c.url.startsWith("/api/dev/whatsapp/messages"))!.url).toBe("/api/dev/whatsapp/messages");
  });

  it("shows send errors", async () => {
    mockApi({ "GET /api/me": [401, {}], "GET /api/dev/whatsapp/messages": { items: [] },
              "POST /api/dev/whatsapp/send": [422, { detail: "Log in, or give a test phone number" }] });
    render(<Simulator />);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Message"), "hi");
    await user.click(screen.getByText("Send"));
    expect(await screen.findByText("Log in, or give a test phone number")).toBeTruthy();
  });

  it("uploads media with the test phone and closes the panel", async () => {
    const calls = mockApi({ "GET /api/me": [401, {}], "GET /api/dev/whatsapp/messages": { items: [] },
                            "POST /api/dev/whatsapp/media": { sent: 1 } });
    render(<Simulator />);
    const user = userEvent.setup();
    await user.click(await screen.findByTitle("Attach clips or photos"));
    await user.upload(screen.getByLabelText("Videos or photos from your computer"),
                      new File(["x"], "a.mp4", { type: "video/mp4" }));
    await user.type(screen.getByLabelText("Caption (optional)"), "that's all");
    fireEvent.submit(screen.getByText("Send files").closest("form")!); // jsdom: uploaded files don't satisfy `required`
    await waitFor(() => expect(screen.queryByText("Send files")).toBeNull());
    const fd: FormData = calls.find((c) => c.url === "/api/dev/whatsapp/media")!.body;
    expect(fd.get("phone")).toBe("+15557654321");
    expect(fd.get("caption")).toBe("that's all");
    expect(fd.has("files")).toBe(true); // jsdom drops the uploaded File from FormData; the field is still sent
  });

  it("sends sample footage", async () => {
    const calls = mockApi({ "GET /api/me": [401, {}], "GET /api/dev/whatsapp/messages": { items: [] },
                            "POST /api/dev/whatsapp/samples": { sent: 3 } });
    render(<Simulator />);
    await userEvent.click(await screen.findByTitle("Attach clips or photos"));
    await userEvent.click(screen.getByText("Send sample footage"));
    await waitFor(() => expect(calls.some((c) => c.url.startsWith("/api/dev/whatsapp/samples"))).toBe(true));
    expect(calls.find((c) => c.url.startsWith("/api/dev/whatsapp/samples"))!.url)
      .toBe("/api/dev/whatsapp/samples?phone=%2B15557654321");
  });

  it("sends a voice note; panels toggle", async () => {
    const calls = mockApi({ "GET /api/me": me({ phone: "+15557654321" }),
                            "GET /api/dev/whatsapp/messages": { items: [] },
                            "POST /api/dev/whatsapp/voice": { accepted: 1 } });
    render(<Simulator />);
    const user = userEvent.setup();
    const mic = await screen.findByTitle("Voice note");
    await user.click(mic);
    await user.click(mic);
    expect(screen.queryByText("Send voice note")).toBeNull();
    await user.click(mic);
    await user.upload(screen.getByLabelText(/Audio file/), new File(["OggS"], "v.ogg", { type: "audio/ogg" }));
    await user.type(screen.getByLabelText(/What it says/), "that's all");
    fireEvent.submit(screen.getByText("Send voice note").closest("form")!);
    await waitFor(() => expect(calls.some((c) => c.url === "/api/dev/whatsapp/voice")).toBe(true));
    const fd: FormData = calls.find((c) => c.url === "/api/dev/whatsapp/voice")!.body;
    expect(fd.get("transcript")).toBe("that's all");
    expect(fd.has("phone")).toBe(false); // logged in: backend uses the account number
  });

  it("polls for new messages", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const calls = mockApi({ "GET /api/me": [401, {}], "GET /api/dev/whatsapp/messages": { items: [] } });
    render(<Simulator />);
    await screen.findByText(/Send some clips to start/);
    const n = calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500);
    });
    expect(calls.length).toBeGreaterThan(n);
  });
});
