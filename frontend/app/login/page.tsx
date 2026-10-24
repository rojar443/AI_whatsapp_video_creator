"use client";

import Link from "next/link";
import QRCode from "qrcode";
import { FormEvent, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useConfig } from "@/lib/useConfig";

type Start = { challenge_id: string; deep_link: string; expires_in_minutes: number };
type Phase = "idle" | "waiting" | "expired";

function nextUrl(): string {
  const next = new URLSearchParams(window.location.search).get("next");
  return next && next.startsWith("/") && !next.startsWith("//") ? next : "/dashboard";
}

export default function Login() {
  const cfg = useConfig();
  const [terms, setTerms] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [start, setStart] = useState<Start | null>(null);
  const [qr, setQr] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  function stopPolling() {
    if (timer.current) clearInterval(timer.current);
    timer.current = null;
  }
  useEffect(() => stopPolling, []);

  // The same one-time link as the button, as a QR code so a computer user can scan it with their phone.
  useEffect(() => {
    if (!start) {
      setQr("");
      return;
    }
    QRCode.toDataURL(start.deep_link, { margin: 1, width: 240, color: { dark: "#1b1340", light: "#ffffff" } })
      .then(setQr)
      .catch(() => setQr(""));
  }, [start]);

  async function begin() {
    setBusy(true);
    setError("");
    try {
      const s = await api<Start>("/api/auth/telegram/start", { method: "POST", json: { accept_terms: terms } });
      setStart(s);
      setPhase("waiting");
      timer.current = setInterval(async () => {
        try {
          const r = await api<{ status: "pending" | "confirmed" | "expired" }>(
            `/api/auth/telegram/status?id=${encodeURIComponent(s.challenge_id)}`,
          );
          if (r.status === "confirmed") {
            stopPolling();
            window.location.href = nextUrl();
          } else if (r.status === "expired") {
            stopPolling();
            setPhase("expired");
          }
        } catch (e: any) {
          stopPolling();
          setError(e.message);
          setPhase("idle");
        }
      }, 2000);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function devLogin(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const tgId = String(f.get("tg_id")).trim();
    if (!/^\d{3,16}$/.test(tgId)) {
      setError("Enter your numeric Telegram ID (digits only, like 123456789), not your @username. Message @userinfobot in Telegram to get it.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api("/api/dev/telegram/login", { method: "POST", json: { tg_id: tgId, name: f.get("name") || undefined } });
      window.location.href = nextUrl();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function emailLogin(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true);
    setError("");
    try {
      await api("/api/auth/login", { method: "POST", json: { email: f.get("email"), password: f.get("password") } });
      window.location.href = nextUrl();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const telegramReady = cfg ? Boolean(cfg.telegram_bot) : true;

  return (
    <div className="container narrow">
      <div className="card stack">
        <h1>Log in with Telegram</h1>
        <p className="muted" style={{ margin: 0 }}>
          No password and no email. Press <strong>Start</strong> in our Telegram bot and you're in. Reels you made in
          the chat are waiting on your dashboard.
        </p>
        {error && <div className="alert error" role="alert">{error}</div>}
        {!telegramReady && <div className="alert warn">Telegram login isn't set up on this server yet.</div>}

        {phase === "idle" && telegramReady && (
          <>
            <label className="check">
              <input type="checkbox" checked={terms} onChange={(e) => setTerms(e.target.checked)} />
              <span>I agree to the <Link href="/terms">Terms</Link> and <Link href="/privacy">Privacy Policy</Link>.</span>
            </label>
            <button className="btn tg" disabled={!terms || busy} onClick={begin} style={{ width: "100%" }}>
              {busy ? "One moment…" : "Continue with Telegram"}
            </button>
          </>
        )}

        {phase === "waiting" && start && (
          <>
            <a className="btn tg" href={start.deep_link} target="_blank" rel="noreferrer" style={{ width: "100%" }}>
              Open Telegram
            </a>
            {qr && (
              <div style={{ textAlign: "center" }}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={qr} width={240} height={240} alt="QR code: scan with your phone to open the bot in Telegram"
                  style={{ border: "2.5px solid var(--text)", borderRadius: 14, boxShadow: "var(--pop-sm)" }} />
                <p className="small muted" style={{ marginTop: 8 }}>On a computer? Scan this with your phone.</p>
              </div>
            )}
            <div className="alert warn" role="status">
              Waiting for you to press <strong>Start</strong> in Telegram… this page continues by itself. The link works
              for {start.expires_in_minutes} minutes.
            </div>
          </>
        )}

        {phase === "expired" && (
          <>
            <div className="alert error" role="alert">That link expired.</div>
            <button className="btn" onClick={() => { setPhase("idle"); setStart(null); }} style={{ width: "100%" }}>
              Try again
            </button>
          </>
        )}

        {cfg?.dev_mode && (
          <form onSubmit={devLogin} className="alert warn" style={{ marginBottom: 0 }}>
            <strong>Development only:</strong> sign in with your Telegram user ID (ask @userinfobot) without the bot.
            <div className="row" style={{ marginTop: 8 }}>
              <input name="tg_id" required inputMode="numeric" placeholder="Numeric ID, e.g. 123456789" aria-label="Telegram user ID"
                     style={{ flex: "1 1 160px" }} />
              <input name="name" placeholder="Name (optional)" aria-label="Name" style={{ flex: "1 1 120px" }} />
              <button className="btn small" disabled={busy}>Sign in</button>
            </div>
          </form>
        )}

        <details>
          <summary className="small muted" style={{ cursor: "pointer", fontWeight: 700 }}>
            I have an email account instead
          </summary>
          <form onSubmit={emailLogin} style={{ marginTop: 12 }}>
            <div className="field">
              <label htmlFor="email">Email</label>
              <input id="email" name="email" type="email" required autoComplete="email" />
            </div>
            <div className="field">
              <label htmlFor="password">Password</label>
              <input id="password" name="password" type="password" required autoComplete="current-password" />
            </div>
            <button className="btn secondary" disabled={busy} style={{ width: "100%" }}>Log in with email</button>
          </form>
        </details>
      </div>
    </div>
  );
}
