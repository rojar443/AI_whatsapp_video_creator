"use client";

import { useState } from "react";
import { api, Me } from "@/lib/api";

type LinkInfo = { code: string; wa_me_url: string; expires_in_minutes: number; send_from: string; message: string };

export default function WhatsAppCard({ me, onChange }: { me: Me; onChange: () => void }) {
  const [info, setInfo] = useState<LinkInfo | null>(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");

  async function start() {
    setError("");
    try {
      const r = await api<LinkInfo & { status: string }>("/api/me/whatsapp/link", { method: "POST" });
      if (r.status === "linked") onChange();
      else setInfo(r);
    } catch (e: any) {
      setError(e.message);
    }
  }

  async function resend() {
    try {
      const r = await api<{ dev_verification_url?: string }>("/api/auth/resend-verification", { method: "POST" });
      setNote(r.dev_verification_url ? `Dev mode: ${r.dev_verification_url}` : "Verification email sent.");
    } catch (e: any) {
      setError(e.message);
    }
  }

  if (me.whatsapp.status === "linked") {
    return (
      <div className="card">
        <div className="row spread">
          <h3 style={{ margin: 0 }}>WhatsApp</h3>
          <span className="badge linked">Connected</span>
        </div>
        <p className="muted small" style={{ margin: "8px 0 12px" }}>
          {me.whatsapp.number} · send your ideas to the ReelChat number.
        </p>
        <a className="btn" href={`https://wa.me/${me.whatsapp.business_number}`} target="_blank" rel="noreferrer">
          Open chat
        </a>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="row spread">
        <h3 style={{ margin: 0 }}>Connect WhatsApp</h3>
        <span className="badge pending">{me.whatsapp.status === "pending" ? "Pending" : "Not connected"}</span>
      </div>
      {error && <div className="alert error" style={{ marginTop: 10 }}>{error}</div>}
      {note && <div className="alert ok" style={{ marginTop: 10, wordBreak: "break-all" }}>{note}</div>}
      {!me.email_verified ? (
        <>
          <p className="muted small" style={{ margin: "8px 0 12px" }}>Verify your email first, then link your number.</p>
          <button className="btn secondary" onClick={resend}>Resend verification email</button>
        </>
      ) : info ? (
        <div className="stack" style={{ marginTop: 10 }}>
          <p style={{ margin: 0 }}>
            From <strong>{info.send_from}</strong>, send this message to ReelChat on WhatsApp:
          </p>
          <div className="code-box">{info.message}</div>
          <p className="small muted" style={{ margin: 0 }}>Code expires in {info.expires_in_minutes} minutes.</p>
          <div className="row">
            <a className="btn" href={info.wa_me_url} target="_blank" rel="noreferrer">Open WhatsApp</a>
            <button className="btn secondary" onClick={onChange}>I've sent it</button>
          </div>
        </div>
      ) : (
        <>
          <p className="muted small" style={{ margin: "8px 0 12px" }}>
            We'll give you a one-time code to send from {me.phone}. This proves the number is yours and lets us reply.
          </p>
          <button className="btn" onClick={start}>Get link code</button>
        </>
      )}
    </div>
  );
}
