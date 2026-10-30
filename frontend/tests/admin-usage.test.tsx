import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { me, mockApi } from "./helpers";

vi.mock("next/navigation", () => ({ useParams: () => ({ userId: "owner" }), usePathname: () => "/admin/users/owner" }));
vi.mock("next/link", () => ({ default: ({ href, children }: any) => <a href={href}>{children}</a> }));
import UserUsage from "@/app/admin/users/[userId]/page";

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
