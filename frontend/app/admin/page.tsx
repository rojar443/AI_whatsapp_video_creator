"use client";

import { useCallback, useEffect, useState } from "react";
import { api, fmtDate } from "@/lib/api";
import { useMe } from "@/lib/useMe";

type Tab = "users" | "providers" | "usage" | "jobs" | "health" | "audit";

export default function Admin() {
  const { me } = useMe({ required: true });
  const [tab, setTab] = useState<Tab>("users");
  if (!me) return <p className="muted">Loading…</p>;
  if (me.role !== "admin") return <div className="alert error">Admins only.</div>;
  const tabs: [Tab, string][] = [["users", "Users"], ["providers", "Providers"], ["usage", "Usage & cost"],
    ["jobs", "Jobs"], ["health", "Health"], ["audit", "Audit log"]];
  return (
    <div>
      <h1>Admin</h1>
      <div className="tabs" role="tablist">
        {tabs.map(([k, l]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>{l}</button>
        ))}
      </div>
      {tab === "users" && <Users />}
      {tab === "providers" && <Providers />}
      {tab === "usage" && <UsageTab />}
      {tab === "jobs" && <Jobs />}
      {tab === "health" && <Health />}
      {tab === "audit" && <Audit />}
    </div>
  );
}

function useLoad<T>(path: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(() => {
    api<T>(path).then(setData).catch((e) => setError(e.message));
  }, [path]);
  useEffect(load, [load]);
  return { data, error, load, setError };
}

function Users() {
  const [q, setQ] = useState("");
  const { data, error, load, setError } = useLoad<{ items: any[] }>(`/api/admin/users?q=${encodeURIComponent(q)}`);
  async function patch(id: string, body: object) {
    try {
      await api(`/api/admin/users/${id}`, { method: "PATCH", json: body });
      load();
    } catch (e: any) {
      setError(e.message);
    }
  }
  return (
    <div className="card">
      <div className="row" style={{ marginBottom: 12 }}>
        <input placeholder="Search name or email" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 320 }} />
      </div>
      {error && <div className="alert error">{error}</div>}
      <div className="table-wrap">
        <table>
          <thead><tr><th>User</th><th>Role</th><th>Status</th><th>Credits</th><th>WhatsApp</th><th>Jobs</th><th>Joined</th><th /></tr></thead>
          <tbody>
            {data?.items.map((u) => (
              <tr key={u.id}>
                <td>{u.name}<div className="small muted">{u.email}</div></td>
                <td>{u.role}</td>
                <td><span className={`badge ${u.status === "active" ? "ok" : "failed"}`}>{u.status}</span></td>
                <td>{u.credits}</td>
                <td>{u.whatsapp_linked ? "linked" : "—"}</td>
                <td>{u.jobs}</td>
                <td className="small">{fmtDate(u.created_at)}</td>
                <td className="row">
                  <button className="btn secondary small" onClick={() => patch(u.id, { credits_delta: 10 })}>+10 credits</button>
                  <button className="btn secondary small" onClick={() => patch(u.id, { status: u.status === "active" ? "suspended" : "active" })}>
                    {u.status === "active" ? "Suspend" : "Reactivate"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Providers() {
  const { data, error, load, setError } = useLoad<any>("/api/admin/providers");
  const [ok, setOk] = useState("");
  async function save(category: string, provider: string, model: string, task = "default", reset = false) {
    setError("");
    setOk("");
    try {
      await api("/api/admin/providers", { method: "PUT", json: { category, provider, model: model || null, task, reset } });
      setOk(reset ? `${category}: reverted to environment config` : `${category}: switched to ${provider}`);
      load();
    } catch (e: any) {
      setError(e.message);
    }
  }
  if (!data) return <p className="muted">Loading…</p>;
  return (
    <div className="stack">
      <p className="muted small">
        Changes apply to new messages and jobs immediately. API keys stay in server environment variables and are never shown here.
      </p>
      {error && <div className="alert error">{error}</div>}
      {ok && <div className="alert ok">{ok}</div>}
      <div className="grid grid-2">
        {Object.entries(data.available as Record<string, string[]>).map(([cat, options]) => (
          <ProviderForm key={cat} category={cat} options={options} active={data.active[cat]} onSave={save}
                        tasks={cat === "agent" ? data.agent_tasks : ["default"]} />
        ))}
      </div>
    </div>
  );
}

function ProviderForm({ category, options, active, onSave, tasks }: any) {
  const [provider, setProvider] = useState(active.provider);
  const [task, setTask] = useState("default");
  const [model, setModel] = useState(active.models?.default ?? "");
  return (
    <div className="card">
      <div className="row spread">
        <h3 style={{ margin: 0, textTransform: "capitalize" }}>{category}</h3>
        <span className="badge">{active.source === "db" ? "admin override" : "from env"}</span>
      </div>
      <p className="small muted">Active: <strong>{active.provider}</strong>
        {active.models && Object.keys(active.models).length > 0 &&
          " · " + Object.entries(active.models).map(([t, m]) => `${t}: ${m}`).join(", ")}</p>
      <div className="field">
        <label>Provider</label>
        <select value={provider} onChange={(e) => setProvider(e.target.value)}>
          {options.map((o: string) => <option key={o}>{o}</option>)}
        </select>
      </div>
      {category === "agent" && (
        <div className="row">
          <div style={{ flex: 1 }} className="field">
            <label>Task</label>
            <select value={task} onChange={(e) => { setTask(e.target.value); setModel(active.models?.[e.target.value] ?? ""); }}>
              {tasks.map((t: string) => <option key={t}>{t}</option>)}
            </select>
          </div>
          <div style={{ flex: 2 }} className="field">
            <label>Model (optional)</label>
            <input value={model} onChange={(e) => setModel(e.target.value)} placeholder="e.g. claude-opus-5-5" />
          </div>
        </div>
      )}
      <div className="row">
        <button className="btn small" onClick={() => onSave(category, provider, model, task)}>Save</button>
        {active.source === "db" && (
          <button className="btn secondary small" onClick={() => onSave(category, provider, "", task, true)}>Reset to env</button>
        )}
      </div>
    </div>
  );
}

function UsageTab() {
  const { data } = useLoad<any>("/api/admin/usage?days=30");
  if (!data) return <p className="muted">Loading…</p>;
  return (
    <div className="stack">
      <div className="grid grid-3">
        <div className="card"><div className="muted small">Users</div><h2>{data.users}</h2></div>
        <div className="card"><div className="muted small">Estimated provider cost (30 d)</div><h2>${data.total_cost_usd.toFixed(2)}</h2></div>
        <div className="card"><div className="muted small">Jobs (30 d)</div>
          <h2>{Object.values(data.jobs as Record<string, number>).reduce((a, b) => a + b, 0)}</h2>
          <div className="small muted">{Object.entries(data.jobs).map(([k, v]) => `${k}: ${v}`).join(" · ")}</div></div>
      </div>
      <div className="card table-wrap">
        <table>
          <thead><tr><th>Kind</th><th>Provider</th><th>Events</th><th>Quantity</th><th>Credits</th><th>Est. cost</th></tr></thead>
          <tbody>
            {data.by_kind.map((r: any, i: number) => (
              <tr key={i}><td>{r.kind}</td><td>{r.provider ?? "—"}</td><td>{r.count}</td><td>{Math.round(r.quantity)}</td><td>{r.credits}</td><td>${r.cost_usd.toFixed(4)}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Jobs() {
  const [status, setStatus] = useState("failed");
  const { data, error, load, setError } = useLoad<any>(`/api/admin/jobs?status=${status}`);
  async function retry(id: string) {
    try {
      await api(`/api/admin/jobs/${id}/retry`, { method: "POST" });
      load();
    } catch (e: any) {
      setError(e.message);
    }
  }
  return (
    <div className="stack">
      <div className="row">
        <label htmlFor="st" style={{ margin: 0 }}>Status</label>
        <select id="st" value={status} onChange={(e) => setStatus(e.target.value)} style={{ maxWidth: 200 }}>
          {["", "failed", "queued", "processing", "completed", "cancelled"].map((s) => <option key={s} value={s}>{s || "all"}</option>)}
        </select>
      </div>
      {error && <div className="alert error">{error}</div>}
      <div className="card table-wrap">
        <table>
          <thead><tr><th>Project</th><th>User</th><th>Status</th><th>Attempts</th><th>Error</th><th>Created</th><th /></tr></thead>
          <tbody>
            {data?.items.map((j: any) => (
              <tr key={j.id}>
                <td>{j.project_title}<div className="small muted">rev {j.revision} · run {j.run_number}</div></td>
                <td className="small">{j.user_email}</td>
                <td><span className={`badge ${j.status}`}>{j.status}</span></td>
                <td>{j.attempts}</td>
                <td className="small">{j.error_code && <code>{j.error_code}</code>} {j.error_message}</td>
                <td className="small">{fmtDate(j.created_at)}</td>
                <td>{j.status === "failed" && <button className="btn small" onClick={() => retry(j.id)}>Retry</button>}</td>
              </tr>
            ))}
            {data?.items.length === 0 && <tr><td colSpan={7} className="muted">None.</td></tr>}
          </tbody>
        </table>
      </div>
      {data?.dead_tasks?.length > 0 && (
        <div className="card table-wrap">
          <h3>Dead-letter tasks</h3>
          <table>
            <thead><tr><th>ID</th><th>Type</th><th>Attempts</th><th>Last error</th><th>At</th></tr></thead>
            <tbody>
              {data.dead_tasks.map((t: any) => (
                <tr key={t.id}><td>{t.id}</td><td>{t.type}</td><td>{t.attempts}</td><td className="small">{t.error}</td><td className="small">{fmtDate(t.at)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Health() {
  const { data, load } = useLoad<any>("/api/admin/health");
  if (!data) return <p className="muted">Checking providers…</p>;
  return (
    <div className="stack">
      <button className="btn secondary small" onClick={load}>Re-check</button>
      <div className="grid grid-3">
        {Object.entries(data.providers).map(([k, v]: [string, any]) => (
          <div className="card" key={k}>
            <div className="row spread">
              <h3 style={{ margin: 0, textTransform: "capitalize" }}>{k}</h3>
              <span className={`badge ${v.ok ? "ok" : "failed"}`}>{v.ok ? "healthy" : "problem"}</span>
            </div>
            <p className="small muted" style={{ margin: "8px 0 0" }}>
              {v.provider}{v.mock ? " (mock)" : ""}{v.source ? ` · ${v.source}` : ""}
              {v.error && <><br /><span style={{ color: "var(--danger)" }}>{v.error}</span></>}
              {v.note && <><br />{v.note}</>}
            </p>
          </div>
        ))}
        <div className="card">
          <h3>Queue</h3>
          <p className="small muted" style={{ margin: 0 }}>
            {Object.entries(data.queue).map(([k, v]) => `${k}: ${v}`).join(" · ") || "empty"}
          </p>
        </div>
      </div>
    </div>
  );
}

function Audit() {
  const { data } = useLoad<any>("/api/admin/audit-logs?limit=200");
  return (
    <div className="card table-wrap">
      <table>
        <thead><tr><th>When</th><th>Action</th><th>Actor</th><th>Target</th><th>Details</th></tr></thead>
        <tbody>
          {data?.items.map((a: any) => (
            <tr key={a.id}>
              <td className="small">{fmtDate(a.at)}</td>
              <td><code>{a.action}</code></td>
              <td className="small">{a.actor?.slice(0, 8) ?? "—"}</td>
              <td className="small">{a.target_type} {a.target_id?.slice(0, 8)}</td>
              <td className="small"><code>{JSON.stringify(a.details)}</code></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
