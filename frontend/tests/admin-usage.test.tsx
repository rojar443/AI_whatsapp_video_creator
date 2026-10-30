import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { me, mockApi } from "./helpers";

vi.mock("next/navigation", () => ({ useParams: () => ({ userId: "owner" }), usePathname: () => "/admin/users/owner" }));
vi.mock("next/link", () => ({ default: ({ href, children, ...props }: any) => <a href={href} {...props}>{children}</a> }));
import UserUsage from "@/app/admin/users/[userId]/page";
import Admin from "@/app/admin/page";

const usage = { tokens: 120, input_tokens: 100, output_tokens: 20, split_complete: true,
  cost_usd: 0.000072, cost_complete: true, llm_calls: 1 };
const data = {
  user: { id: "owner", name: "Owner" },
  summary: { ...usage, messages: 3, projects: 26, reels: 1 },
  unattributed: { ...usage, tokens: 0, messages: 0 },
  items: [{ ...usage, id: "project-reference", status: "completed", created_at: "2026-10-31T00:00:00Z", messages: 3,
    revisions: [{ revision: 1, messages: 3, tokens: 120, attempts: 2,
      tasks: [{ ...usage, task: "script", provider: "openai", model: "gpt-4.1-mini" }] }] }],
  total: 26, limit: 25, offset: 0,
};

it("shows expandable metadata and fetches no media", async () => {
  const calls = mockApi({ "GET /api/me": me({ role: "admin" }), "GET /api/admin/users/owner/usage": data });
  const { container } = render(<UserUsage />);
  expect(await screen.findByText("User usage: Owner")).toBeTruthy();
  await userEvent.click(screen.getByText("Revision breakdown for project-"));
  expect(container.querySelector("details")?.open).toBe(true);
  expect(screen.getByText("Revision 1")).toBeTruthy();
  expect(screen.getByText("script")).toBeTruthy();
  expect(container.querySelector("video, img, source")).toBeNull();
  expect([...container.querySelectorAll("a")].map(a => a.getAttribute("href"))).toEqual(["/admin"]);
  expect(calls.every(c => c.url === "/api/me" || c.url.startsWith("/api/admin/users/owner/usage?"))).toBe(true);
  await userEvent.click(screen.getByText("Next"));
  await waitFor(() => expect(calls.some(c => c.url.includes("offset=25"))).toBe(true));
});

it("never requests usage for a non-admin", async () => {
  const calls = mockApi({ "GET /api/me": me() });
  render(<UserUsage />);
  expect(await screen.findByText("This area is for admins only.")).toBeTruthy();
  expect(calls.some(c => c.url.includes("/usage"))).toBe(false);
});

it("shows an API error without rendering project data", async () => {
  mockApi({ "GET /api/me": me({ role: "admin" }), "GET /api/admin/users/owner/usage": [404, { detail: "User not found" }] });
  render(<UserUsage />);
  expect(await screen.findByRole("alert")).toHaveProperty("textContent", "User not found");
  expect(screen.queryByText("project-")).toBeNull();
});

it("flags affected projects and loads failure details only when opened", async () => {
  const logUrl = "GET /api/admin/users/owner/projects/project-reference/ai-issues";
  const calls = mockApi({ "GET /api/me": me({ role: "admin" }),
    "GET /api/admin/users/owner/usage": { ...data, items: [{ ...data.items[0], ai_issue_count: 1 }] },
    [logUrl]: { total: 1, historical: [], items: [{ id: "event", revision: 1, task: "vision", provider: "gemini",
      model: "test-model", code: "request_failed", created_at: "2026-10-31T00:00:00Z", historical: false,
      error: "429 RESOURCE_EXHAUSTED: quota exceeded" }] },
  });
  const { container } = render(<UserUsage />);
  const toggle = await screen.findByText("AI fallback logs (1)");
  expect(screen.getAllByRole("img", { name: "AI fallback detected" })).toHaveLength(2);
  expect(calls.some(c => c.url.includes("ai-issues"))).toBe(false);
  await userEvent.click(toggle);
  const event = await screen.findByText(/Revision 1 · vision/);
  await userEvent.click(event);
  expect(screen.getByText("429 RESOURCE_EXHAUSTED: quota exceeded")).toBeTruthy();
  expect(screen.getByText(/gemini \/ test-model/)).toBeTruthy();
  expect(container.querySelector("video, img, source")).toBeNull();
});

it("lets admins retry loading failure logs", async () => {
  mockApi({ "GET /api/me": me({ role: "admin" }),
    "GET /api/admin/users/owner/usage": { ...data, items: [{ ...data.items[0], ai_issue_count: 1 }] },
    "GET /api/admin/users/owner/projects/project-reference/ai-issues": [500, { detail: "Logs unavailable" }],
  });
  render(<UserUsage />);
  await userEvent.click(await screen.findByText("AI fallback logs (1)"));
  expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Logs unavailable Retry logs");
  expect(screen.getByRole("button", { name: "Retry logs" })).toBeTruthy();
});

it("shows a linked fallback icon beside an affected user", async () => {
  mockApi({ "GET /api/me": me({ role: "admin" }),
    "GET /api/admin/summary": { users: 1, talked_to_bot: 1, tokens: 0, cost_usd: 0, by_provider: [] },
    "GET /api/admin/users": { items: [{ id: "owner", name: "Owner", channels: [], registered: true,
      role: "user", status: "active", projects: 1, reels: 1, messages: 3, tokens: 0, cost_usd: 0,
      credits: 0, ai_issue_count: 1 }], total: 1 },
  });
  render(<Admin />);
  expect((await screen.findByRole("link", { name: "View AI fallback issues for Owner" })).getAttribute("href")).toBe("/admin/users/owner");
});

it("opens the raw traceback on demand beside a fallback event", async () => {
  const raw = "Traceback (most recent call last):\n  File scoring.py, in run\nAgentError: 429 quota exceeded";
  const calls = mockApi({ "GET /api/me": me({ role: "admin" }),
    "GET /api/admin/users/owner/usage": { ...data, items: [{ ...data.items[0], ai_issue_count: 1 }] },
    "GET /api/admin/users/owner/projects/project-reference/ai-issues": { total: 1, historical: [], items: [
      { id: "event", revision: 1, task: "vision", historical: false, error: "quota exceeded" }] },
    "GET /api/admin/users/owner/projects/project-reference/ai-issues/event/raw": { available: true, raw_log: raw },
  });
  const { container } = render(<UserUsage />);
  await userEvent.click(await screen.findByText("AI fallback logs (1)"));
  const toggle = await screen.findByRole("button", { name: "View raw log" });
  expect(calls.some(c => c.url.endsWith("/raw"))).toBe(false);
  await userEvent.click(toggle);
  await screen.findByRole("region", { name: "Raw log for revision 1 vision" });
  await waitFor(() => expect(container.querySelector("pre code")?.textContent).toBe(raw));
  await userEvent.click(screen.getByRole("button", { name: "Hide raw log" }));
  expect(container.querySelector("pre")).toBeNull();
});

it("explains missing historical raw logs without making a raw API request", async () => {
  const calls = mockApi({ "GET /api/me": me({ role: "admin" }),
    "GET /api/admin/users/owner/usage": { ...data, items: [{ ...data.items[0], ai_issue_count: 1 }] },
    "GET /api/admin/users/owner/projects/project-reference/ai-issues": { total: 0, items: [], historical: [
      { id: "historical-1", revision: 1, task: "vision", historical: true, error: "Original cause not recorded" }] },
  });
  render(<UserUsage />);
  await userEvent.click(await screen.findByText("AI fallback logs (1)"));
  await userEvent.click(await screen.findByRole("button", { name: "View raw log" }));
  expect(await screen.findByText(/No raw log was recorded for this event/)).toBeTruthy();
  expect(calls.some(c => c.url.endsWith("/raw"))).toBe(false);
});
