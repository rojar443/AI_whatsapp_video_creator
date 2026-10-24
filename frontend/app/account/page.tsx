"use client";

import { FormEvent, useEffect, useState } from "react";
import WhatsAppCard from "@/components/WhatsAppCard";
import { api, fmtDate } from "@/lib/api";
import { useMe } from "@/lib/useMe";

type Usage = { credits: number; max_jobs_per_day: number; ledger: { kind: string; credits: number; at: string }[] };
const KIND: Record<string, string> = {
  credit_grant: "Sign-up bonus", credit_reserve: "Video generation", credit_refund: "Refund", credit_adjust: "Adjustment",
};

export default function Account() {
  const { me, refresh } = useMe({ required: true });
  const [usage, setUsage] = useState<Usage | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (me) api<Usage>("/api/me/usage").then(setUsage).catch(() => {});
  }, [me]);

  async function unlink() {
    if (!confirm("Unlink WhatsApp? You'll stop receiving messages from ReelChat.")) return;
    await api("/api/me/whatsapp", { method: "DELETE" });
    refresh();
  }

  async function del(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const password = new FormData(e.currentTarget).get("password");
    if (!confirm("Permanently delete your account, messages, projects and videos?")) return;
    try {
      await api("/api/me/delete", { method: "POST", json: { password } });
      window.location.href = "/";
    } catch (err: any) {
      setError(err.message);
    }
  }

  if (!me) return <p className="muted">Loading…</p>;
  return (
    <div className="stack">
      <h1>Account</h1>
      <div className="grid grid-2">
        <div className="card">
          <h3>Profile</h3>
          <dl className="kv">
            <dt>Name</dt><dd>{me.name}</dd>
            <dt>Email</dt><dd>{me.email} {me.email_verified ? <span className="badge ok">verified</span> : <span className="badge pending">unverified</span>}</dd>
            <dt>WhatsApp</dt><dd>{me.phone}</dd>
          </dl>
        </div>
        <WhatsAppCard me={me} onChange={refresh} />
      </div>

      <div className="card">
        <div className="row spread">
          <h3 style={{ margin: 0 }}>Credits & usage</h3>
          <span className="badge ok">{me.credits} credits</span>
        </div>
        <p className="small muted">
          15 s = 1 credit at standard quality; high quality costs double. Limit: {usage?.max_jobs_per_day ?? "—"} videos/day.
        </p>
        <div className="table-wrap">
          <table>
            <thead><tr><th>When</th><th>What</th><th>Credits</th></tr></thead>
            <tbody>
              {usage?.ledger.map((l, i) => (
                <tr key={i}><td>{fmtDate(l.at)}</td><td>{KIND[l.kind] ?? l.kind}</td><td>{l.credits > 0 ? `+${l.credits}` : l.credits}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <h3>Privacy controls</h3>
        {me.whatsapp.status === "linked" && (
          <p><button className="btn secondary" onClick={unlink}>Unlink WhatsApp</button></p>
        )}
        {error && <div className="alert error">{error}</div>}
        <form onSubmit={del} className="row" style={{ alignItems: "flex-end" }}>
          <div style={{ flex: "1 1 220px" }}>
            <label htmlFor="pw">Delete account (enter password)</label>
            <input id="pw" name="password" type="password" required autoComplete="current-password" />
          </div>
          <button className="btn danger">Delete everything</button>
        </form>
      </div>
    </div>
  );
}
