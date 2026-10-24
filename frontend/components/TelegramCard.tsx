"use client";

import { useState } from "react";
import { api, Me } from "@/lib/api";

export default function TelegramCard({ me, onChange }: { me: Me; onChange: () => void }) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");

  if (!me.telegram.enabled) return null;

  async function start() {
    setError("");
    try {
      const r = await api<{ status: string; url?: string }>("/api/me/telegram/link", { method: "POST" });
      if (r.status === "linked") onChange();
      else setUrl(r.url || "");
    } catch (e: any) {
      setError(e.message);
    }
  }

  async function unlink() {
    await api("/api/me/telegram", { method: "DELETE" });
    setUrl("");
    onChange();
  }

  return (
    <div className="card">
      <div className="row spread">
        <h3 style={{ margin: 0 }}>Telegram</h3>
        <span className={`badge ${me.telegram.linked ? "linked" : "pending"}`}>{me.telegram.linked ? "Connected" : "Not connected"}</span>
      </div>
      {error && <div className="alert error" style={{ marginTop: 10 }}>{error}</div>}
      {me.telegram.linked ? (
        <p style={{ margin: "10px 0 0" }}>
          <button className="btn secondary small" onClick={unlink}>Disconnect</button>
        </p>
      ) : url ? (
        <div className="stack" style={{ marginTop: 10 }}>
          <p className="small muted" style={{ margin: 0 }}>Open the link in Telegram and press <strong>Start</strong>.</p>
          <div className="row">
            <a className="btn" href={url} target="_blank" rel="noreferrer">Open Telegram</a>
            <button className="btn secondary" onClick={onChange}>I've done it</button>
          </div>
        </div>
      ) : (
        <>
          <p className="muted small" style={{ margin: "8px 0 12px" }}>
            Send clips from Telegram too. Connecting moves any reels you already made there into your history.
          </p>
          <button className="btn" onClick={start}>Connect Telegram</button>
        </>
      )}
    </div>
  );
}
