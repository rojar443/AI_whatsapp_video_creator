"use client";

import { useCallback, useEffect, useState } from "react";
import { api, fmtDate } from "@/lib/api";
import { useMe } from "@/lib/useMe";

type Channel = { kind: "telegram" | "whatsapp" | "web"; id: string | null; name: string | null; status: string };
type Row = {
  id: string; name: string; email: string | null; channels: Channel[]; registered: boolean; role: string;
  status: string; credits: number; projects: number; reels: number; messages: number;
  first_seen: string | null; last_seen: string | null;
};
type Page = { items: Row[]; total: number; limit: number; offset: number };

const LIMIT = 25;
const CHANNEL_LABEL: Record<string, string> = { telegram: "Telegram", whatsapp: "WhatsApp", web: "Website" };

export default function Admin() {
  const { me } = useMe({ required: true });
  const [data, setData] = useState<Page | null>(null);
  const [q, setQ] = useState("");
  const [channel, setChannel] = useState("");
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const qs = new URLSearchParams({ limit: String(LIMIT), offset: String(offset) });
      if (q.trim()) qs.set("q", q.trim());
      if (channel) qs.set("channel", channel);
      setData(await api<Page>(`/api/admin/users?${qs}`));
      setError("");
    } catch (e: any) {
      setError(e.message);
    }
  }, [q, channel, offset]);

  useEffect(() => {
    if (me?.role === "admin") {
      const t = setTimeout(load, 250); // debounce typing in the search box
      return () => clearTimeout(t);
    }
  }, [me, load]);

  async function patch(id: string, body: { status?: string; credits_delta?: number }) {
    try {
      await api(`/api/admin/users/${id}`, { method: "PATCH", json: body });
      load();
    } catch (e: any) {
      setError(e.message);
    }
  }

  if (!me) return <p className="muted">Loading…</p>;
  if (me.role !== "admin") return <div className="alert error">This area is for admins only.</div>;

  const total = data?.total ?? 0;
  return (
    <div className="stack">
      <div className="row spread">
        <h1 style={{ margin: 0 }}>Users</h1>
        <span className="badge ok">{total} {total === 1 ? "person has" : "people have"} used the app</span>
      </div>
      <div className="card">
        <div className="row" style={{ marginBottom: 12 }}>
          <input
            placeholder="Search name, @username, Telegram id or email"
            aria-label="Search users"
            value={q}
            onChange={(e) => { setQ(e.target.value); setOffset(0); }}
            style={{ maxWidth: 380 }}
          />
          <select aria-label="Channel" value={channel} onChange={(e) => { setChannel(e.target.value); setOffset(0); }}
                  style={{ maxWidth: 170 }}>
            <option value="">All channels</option>
            <option value="telegram">Telegram</option>
            <option value="whatsapp">WhatsApp</option>
            <option value="web">Website</option>
          </select>
        </div>
        {error && <div className="alert error">{error}</div>}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Person</th><th>Channel</th><th>First seen</th><th>Last seen</th><th>Msgs</th><th>Reels</th>
                <th>Credits</th><th>Status</th><th />
              </tr>
            </thead>
            <tbody>
              {data?.items.map((u) => (
                <tr key={u.id}>
                  <td>
                    <strong>{u.name || "Unknown"}</strong>{" "}
                    {u.role === "admin" && <span className="badge ok">admin</span>}
                    <div className="small muted">{u.email ?? (u.registered ? "" : "bot only")}</div>
                  </td>
                  <td>
                    {u.channels.map((c, i) => (
                      <div key={i} className="small">
                        <span className={`badge ${c.kind === "telegram" ? "linked" : ""}`}>{CHANNEL_LABEL[c.kind]}</span>{" "}
                        {c.id && <span className="muted">{c.id}</span>}
                      </div>
                    ))}
                  </td>
                  <td className="small" title={u.first_seen ?? ""}>{fmtDate(u.first_seen)}</td>
                  <td className="small" title={u.last_seen ?? ""}>{fmtDate(u.last_seen)}</td>
                  <td>{u.messages}</td>
                  <td title={`${u.projects} projects`}>{u.reels}</td>
                  <td>{u.credits}</td>
                  <td><span className={`badge ${u.status === "active" ? "ok" : "failed"}`}>{u.status}</span></td>
                  <td className="row">
                    <button className="btn secondary small" onClick={() => patch(u.id, { credits_delta: 10 })}>+10</button>
                    <button className="btn secondary small" onClick={() => patch(u.id, { credits_delta: -10 })}
                            disabled={u.credits < 10}>−10</button>
                    {u.id !== me.id && (
                      <button className={`btn small ${u.status === "active" ? "danger" : ""}`}
                              onClick={() => patch(u.id, { status: u.status === "active" ? "suspended" : "active" })}>
                        {u.status === "active" ? "Suspend" : "Reactivate"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {data && data.items.length === 0 && (
                <tr><td colSpan={9} className="muted">No one matches.</td></tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="row spread" style={{ marginTop: 12 }}>
          <span className="small muted">
            {total === 0 ? "0" : `${offset + 1}–${Math.min(offset + LIMIT, total)}`} of {total}
          </span>
          <span className="row">
            <button className="btn secondary small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - LIMIT))}>
              Previous
            </button>
            <button className="btn secondary small" disabled={offset + LIMIT >= total} onClick={() => setOffset(offset + LIMIT)}>
              Next
            </button>
          </span>
        </div>
      </div>
    </div>
  );
}
