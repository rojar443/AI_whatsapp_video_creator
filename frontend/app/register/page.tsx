"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { api } from "@/lib/api";

export default function Register() {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [devUrl, setDevUrl] = useState("");

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true);
    setError("");
    try {
      const res = await api<{ dev_verification_url?: string }>("/api/auth/register", {
        method: "POST",
        json: {
          name: f.get("name"),
          email: f.get("email"),
          phone: f.get("phone"),
          password: f.get("password"),
          accept_terms: f.get("terms") === "on",
        },
      });
      if (res.dev_verification_url) setDevUrl(res.dev_verification_url);
      else window.location.href = "/dashboard";
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (devUrl) {
    return (
      <div className="container narrow card">
        <h1>Check your email</h1>
        <p>We sent you a verification link.</p>
        <div className="alert warn">
          Development mode: no email is sent. <a href={devUrl}>Open the verification link</a>.
        </div>
        <Link href="/dashboard">Go to dashboard →</Link>
      </div>
    );
  }

  return (
    <div className="container narrow">
      <div className="card">
        <h1>Create your account</h1>
        <p className="muted">You'll get 10 free credits to try it out.</p>
        {error && <div className="alert error" role="alert">{error}</div>}
        <form onSubmit={submit}>
          <div className="field">
            <label htmlFor="name">Name</label>
            <input id="name" name="name" required maxLength={120} autoComplete="name" />
          </div>
          <div className="field">
            <label htmlFor="email">Email</label>
            <input id="email" name="email" type="email" required autoComplete="email" />
          </div>
          <div className="field">
            <label htmlFor="phone">WhatsApp number</label>
            <input id="phone" name="phone" required placeholder="+91 98765 43210" autoComplete="tel" />
            <p className="small muted" style={{ margin: "4px 0 0" }}>International format, with country code.</p>
          </div>
          <div className="field">
            <label htmlFor="password">Password</label>
            <input id="password" name="password" type="password" required minLength={10} autoComplete="new-password" />
            <p className="small muted" style={{ margin: "4px 0 0" }}>At least 10 characters.</p>
          </div>
          <div className="field">
            <label className="check">
              <input type="checkbox" name="terms" required />
              <span>
                I agree to the <Link href="/terms">Terms</Link> and <Link href="/privacy">Privacy Policy</Link>, including
                AI processing of my messages and voice notes.
              </span>
            </label>
          </div>
          <button className="btn" disabled={busy} style={{ width: "100%" }}>
            {busy ? "Creating account…" : "Create account"}
          </button>
        </form>
        <p className="small muted" style={{ marginTop: 14 }}>
          Already have an account? <Link href="/login">Log in</Link>
        </p>
      </div>
    </div>
  );
}
