"use client";

import { useEffect, useState } from "react";
import { api, fmtDate } from "@/lib/api";

type Issue = {
  id: string; revision: number | null; created_at: string | null; task: string;
  provider: string | null; model: string | null; code: string; error: string; historical: boolean;
};
type Log = { items: Issue[]; historical: Issue[]; total: number };
type Raw = { available: boolean; raw_log: string | null };

function EventLog({ item, userId, projectId }: { item: Issue; userId: string; projectId: string }) {
  const [open, setOpen] = useState(false);
  const [raw, setRaw] = useState<Raw | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!open || item.historical) return;
    let active = true;
    setRaw(null); setError("");
    api<Raw>(`/api/admin/users/${encodeURIComponent(userId)}/projects/${encodeURIComponent(projectId)}/ai-issues/${encodeURIComponent(item.id)}/raw`)
      .then(value => { if (active) setRaw(value); })
      .catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [open, item.id, item.historical, userId, projectId, retry]);
  return <div style={{ margin: "12px 0" }}>
    <div className="row" style={{ alignItems: "baseline", flexWrap: "wrap" }}>
      <details style={{ flex: 1, minWidth: 220 }}>
        <summary>{item.revision === null ? "Unknown revision" : `Revision ${item.revision}`} · {item.task} · {item.historical ? "Historical fallback" : fmtDate(item.created_at)}</summary>
        <p className="small">{item.provider || "Provider not recorded"} / {item.model || "Model not recorded"} · {item.code}</p>
        <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{item.error}</p>
      </details>
      <button className="btn secondary small" aria-expanded={open} aria-controls={`raw-${projectId}-${item.id}`} onClick={() => setOpen(value => !value)}>{open ? "Hide raw log" : "View raw log"}</button>
    </div>
    {open && <div id={`raw-${projectId}-${item.id}`} role="region" aria-label={`Raw log for revision ${item.revision ?? "unknown"} ${item.task}`} style={{ marginTop: 12 }}>
      <p className="small muted">Captured diagnostic output and exception traceback, where available. Credentials and provider URLs are redacted.</p>
      {error && <div role="alert">{error} <button className="btn secondary small" onClick={() => setRetry(n => n + 1)}>Retry raw log</button></div>}
      {!item.historical && !raw && !error && <p role="status">Loading raw log…</p>}
      {(item.historical || (raw && !raw.available)) && <p className="small muted">No raw log was recorded for this event. Historical output cannot be reconstructed. New fallback events will capture diagnostic output.</p>}
      {raw?.available && <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: 480, overflow: "auto", padding: 12, border: "1px solid var(--border)", borderRadius: 8 }}><code>{raw.raw_log}</code></pre>}
    </div>}
  </div>;
}

export default function AiIssues({ userId, projectId, count }: { userId: string; projectId: string; count: number }) {
  const [open, setOpen] = useState(false);
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<Log | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!open) return;
    let active = true;
    setData(null); setError("");
    api<Log>(`/api/admin/users/${encodeURIComponent(userId)}/projects/${encodeURIComponent(projectId)}/ai-issues?limit=25&offset=${offset}`)
      .then(value => { if (active) setData(value); })
      .catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [open, userId, projectId, offset, retry]);
  return <details onToggle={e => setOpen(e.currentTarget.open)}>
    <summary><span role="img" aria-label="AI fallback detected">⚠️</span> AI fallback logs ({count})</summary>
    {open && <div style={{ marginTop: 12 }}>
      <p className="small muted">AI was skipped or fell back during processing. These events include previous revisions and retries; a completed video may have used fallback editing.</p>
      {error && <div role="alert">{error} <button className="btn secondary small" onClick={() => setRetry(n => n + 1)}>Retry logs</button></div>}
      {!data && !error && <p role="status">Loading AI fallback logs…</p>}
      {data && <>
        {[...data.items, ...data.historical].map(item => <EventLog key={item.id} item={item} userId={userId} projectId={projectId} />)}
        {!data.items.length && !data.historical.length && <p>No recorded AI fallback events.</p>}
        {data.total > 25 && <div className="row">
          <button className="btn secondary small" disabled={!offset} onClick={() => setOffset(n => Math.max(0, n - 25))}>Previous logs</button>
          <span>{offset + 1}–{Math.min(offset + 25, data.total)} of {data.total} recorded events</span>
          <button className="btn secondary small" disabled={offset + 25 >= data.total} onClick={() => setOffset(n => n + 25)}>Next logs</button>
        </div>}
      </>}
    </div>}
  </details>;
}
