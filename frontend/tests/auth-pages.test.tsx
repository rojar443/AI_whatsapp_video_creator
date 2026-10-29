import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CONFIG, mockApi, mockLocation } from "./helpers";

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: any) => <a href={href} {...rest}>{children}</a>,
}));
vi.mock("qrcode", () => ({ default: { toDataURL: vi.fn(async () => "data:image/png;base64,QR") } }));

import Login from "@/app/login/page";
import Register from "@/app/register/page";
import VerifyEmail from "@/app/verify-email/page";

let loc: ReturnType<typeof mockLocation>;
beforeEach(() => {
  loc = mockLocation();
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

// ------------------------------------------------------------------ register
async function fillRegister(user = userEvent.setup()) {
  await user.type(screen.getByLabelText("Name"), "Asha");
  await user.type(screen.getByLabelText("Email"), "asha@x.test");
  await user.type(screen.getByLabelText("Password"), "correct horse battery");
  await user.click(screen.getByRole("checkbox"));
  return user;
}

describe("Register page", () => {
  it("submits the form and shows the dev verification link", async () => {
    const calls = mockApi({ "GET /api/config": CONFIG,
                            "POST /api/auth/register": { dev_verification_url: "http://x/verify-email?token=t" } });
    loc.search = "?phone=%2B15557654321";
    render(<Register />);
    expect(await screen.findByLabelText("WhatsApp number (optional)")).toHaveProperty("value", "+15557654321");
    const user = await fillRegister();
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByText("Check your email")).toBeTruthy();
    expect(screen.getByText("Open the verification link").getAttribute("href")).toBe("http://x/verify-email?token=t");
    const body = calls.find((c) => c.url === "/api/auth/register")!.body;
    expect(body).toEqual({ name: "Asha", email: "asha@x.test", phone: "+15557654321",
                           password: "correct horse battery", accept_terms: true });
  });

  it("goes to the dashboard in production (no dev link)", async () => {
    mockApi({ "GET /api/config": CONFIG, "POST /api/auth/register": [201, { user: {} }] });
    render(<Register />);
    const user = await fillRegister();
    await user.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(loc.href).toBe("/dashboard"));
  });

  it("shows API errors and re-enables the button", async () => {
    mockApi({ "GET /api/config": CONFIG,
              "POST /api/auth/register": [409, { detail: "An account with this email already exists" }] });
    render(<Register />);
    const user = await fillRegister();
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect((await screen.findByRole("alert")).textContent).toBe("An account with this email already exists");
    expect(screen.getByRole("button", { name: "Create account" })).toHaveProperty("disabled", false);
  });

  it("hides the phone field when WhatsApp is off", async () => {
    mockApi({ "GET /api/config": { ...CONFIG, whatsapp_enabled: false } });
    render(<Register />);
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(screen.queryByLabelText("WhatsApp number (optional)")).toBeNull();
  });

  it("explains when registration is closed", async () => {
    mockApi({ "GET /api/config": { ...CONFIG, registration_enabled: false } });
    render(<Register />);
    expect(await screen.findByText("Registration is closed")).toBeTruthy();
    expect(screen.getByText("Open in Telegram").getAttribute("href")).toBe("https://t.me/reelbot");
  });

  it("closed registration without a bot shows only the login link", async () => {
    mockApi({ "GET /api/config": { ...CONFIG, registration_enabled: false, telegram_bot: null } });
    render(<Register />);
    await screen.findByText("Registration is closed");
    expect(screen.queryByText("Open in Telegram")).toBeNull();
  });
});

// ------------------------------------------------------------------ verify email
describe("Verify email page", () => {
  it("verifies the token from the URL", async () => {
    loc.search = "?token=abc";
    const calls = mockApi({ "POST /api/auth/verify-email": { ok: true } });
    render(<VerifyEmail />);
    expect(screen.getByText("Verifying…")).toBeTruthy();
    expect(await screen.findByText("Your email is verified.")).toBeTruthy();
    expect(calls[0].body).toEqual({ token: "abc" });
  });

  it("shows the error and a way forward", async () => {
    const calls = mockApi({ "POST /api/auth/verify-email": [400, { detail: "This link has expired" }] });
    render(<VerifyEmail />);
    expect(await screen.findByText(/This link has expired\./)).toBeTruthy();
    expect(calls[0].body).toEqual({ token: "" });
  });
});

// ------------------------------------------------------------------ login
const START = { challenge_id: "c 1", deep_link: "https://t.me/reelbot?start=login_x", expires_in_minutes: 10 };

async function startTelegram() {
  await screen.findByText("Continue with Telegram");
  fireEvent.click(screen.getByRole("checkbox"));
  await act(async () => {
    fireEvent.click(screen.getByText("Continue with Telegram"));
  });
}

async function tick() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000);
  });
}

describe("Login page", () => {
  it("requires accepting the terms before starting", async () => {
    mockApi({ "GET /api/config": CONFIG });
    render(<Login />);
    const btn = await screen.findByText("Continue with Telegram");
    expect(btn).toHaveProperty("disabled", true);
    fireEvent.click(screen.getByRole("checkbox"));
    expect(btn).toHaveProperty("disabled", false);
  });

  it("warns when Telegram isn't configured", async () => {
    mockApi({ "GET /api/config": { ...CONFIG, telegram_bot: null } });
    render(<Login />);
    expect(await screen.findByText("Telegram login isn't set up on this server yet.")).toBeTruthy();
    expect(screen.queryByText("Continue with Telegram")).toBeNull();
  });

  it("polls until confirmed, then follows a safe ?next", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    loc.search = "?next=/dashboard/projects/9";
    let polls = 0;
    const calls = mockApi({
      "GET /api/config": CONFIG, "POST /api/auth/telegram/start": START,
      "GET /api/auth/telegram/status": () => ({ status: ++polls < 2 ? "pending" : "confirmed" }),
    });
    render(<Login />);
    await startTelegram();
    expect(await screen.findByText("Open Telegram")).toHaveProperty("href", START.deep_link);
    expect(await screen.findByAltText(/QR code/)).toHaveProperty("src", "data:image/png;base64,QR");
    expect(screen.getByRole("status").textContent).toContain("10 minutes");
    await tick();
    expect(loc.href).toBe("");
    await tick();
    expect(loc.href).toBe("/dashboard/projects/9");
    expect(calls.find((c) => c.url.startsWith("/api/auth/telegram/status"))!.url).toBe(
      "/api/auth/telegram/status?id=c%201");
    expect(calls.find((c) => c.url === "/api/auth/telegram/start")!.body).toEqual({ accept_terms: true });
    const before = calls.length;
    await tick();
    expect(calls.length).toBe(before); // polling stopped
  });

  it.each([["//evil.example"], ["https://evil.example"], [""]])("ignores unsafe next=%s", async (next) => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    loc.search = next ? `?next=${encodeURIComponent(next)}` : "";
    mockApi({ "GET /api/config": CONFIG, "POST /api/auth/telegram/start": START,
              "GET /api/auth/telegram/status": { status: "confirmed" } });
    render(<Login />);
    await startTelegram();
    await tick();
    expect(loc.href).toBe("/dashboard");
  });

  it("handles expiry and lets the user try again", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockApi({ "GET /api/config": CONFIG, "POST /api/auth/telegram/start": START,
              "GET /api/auth/telegram/status": { status: "expired" } });
    render(<Login />);
    await startTelegram();
    await tick();
    expect(await screen.findByText("That link expired.")).toBeTruthy();
    fireEvent.click(screen.getByText("Try again"));
    expect(await screen.findByText("Continue with Telegram")).toBeTruthy();
  });

  it("stops polling and shows the error when the account is blocked", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockApi({ "GET /api/config": CONFIG, "POST /api/auth/telegram/start": START,
              "GET /api/auth/telegram/status": [403, { detail: "This account is unavailable" }] });
    render(<Login />);
    await startTelegram();
    await tick();
    expect((await screen.findByRole("alert")).textContent).toBe("This account is unavailable");
    expect(screen.getByText("Continue with Telegram")).toBeTruthy();
  });

  it("shows start errors", async () => {
    mockApi({ "GET /api/config": CONFIG,
              "POST /api/auth/telegram/start": [503, { detail: "Telegram login is not available on this server" }] });
    render(<Login />);
    await startTelegram();
    expect((await screen.findByRole("alert")).textContent).toContain("not available");
  });

  it("logs in with email", async () => {
    const calls = mockApi({ "GET /api/config": CONFIG, "POST /api/auth/login": { user: {} } });
    render(<Login />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Email"), "a@x.test");
    await user.type(screen.getByLabelText("Password"), "pw");
    await user.click(screen.getByText("Log in with email"));
    await waitFor(() => expect(loc.href).toBe("/dashboard"));
    expect(calls.find((c) => c.url === "/api/auth/login")!.body).toEqual({ email: "a@x.test", password: "pw" });
  });

  it("shows email login errors", async () => {
    mockApi({ "GET /api/config": CONFIG, "POST /api/auth/login": [401, { detail: "Invalid email or password" }] });
    render(<Login />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Email"), "a@x.test");
    await user.type(screen.getByLabelText("Password"), "pw");
    await user.click(screen.getByText("Log in with email"));
    expect((await screen.findByRole("alert")).textContent).toBe("Invalid email or password");
  });

  describe("dev Telegram-ID login", () => {
    it("is only shown in dev mode", async () => {
      mockApi({ "GET /api/config": CONFIG });
      render(<Login />);
      await screen.findByText("Continue with Telegram");
      expect(screen.queryByLabelText("Telegram user ID")).toBeNull();
    });

    it("rejects @usernames before calling the API", async () => {
      const calls = mockApi({ "GET /api/config": { ...CONFIG, dev_mode: true } });
      render(<Login />);
      const user = userEvent.setup();
      await user.type(await screen.findByLabelText("Telegram user ID"), "@asha");
      await user.click(screen.getByText("Sign in"));
      expect((await screen.findByRole("alert")).textContent).toContain("numeric Telegram ID");
      expect(calls.some((c) => c.url === "/api/dev/telegram/login")).toBe(false);
    });

    it("signs in with a numeric id", async () => {
      const calls = mockApi({ "GET /api/config": { ...CONFIG, dev_mode: true },
                              "POST /api/dev/telegram/login": { status: "confirmed" } });
      render(<Login />);
      const user = userEvent.setup();
      await user.type(await screen.findByLabelText("Telegram user ID"), " 123456789 ");
      await user.click(screen.getByText("Sign in"));
      await waitFor(() => expect(loc.href).toBe("/dashboard"));
      expect(calls.find((c) => c.url === "/api/dev/telegram/login")!.body).toEqual({ tg_id: "123456789" });
    });

    it("shows server errors", async () => {
      mockApi({ "GET /api/config": { ...CONFIG, dev_mode: true },
                "POST /api/dev/telegram/login": [403, { detail: "This account is unavailable" }] });
      render(<Login />);
      const user = userEvent.setup();
      await user.type(await screen.findByLabelText("Telegram user ID"), "123456789");
      await user.type(screen.getByLabelText("Name"), "Me");
      await user.click(screen.getByText("Sign in"));
      expect((await screen.findByRole("alert")).textContent).toBe("This account is unavailable");
    });
  });
});
