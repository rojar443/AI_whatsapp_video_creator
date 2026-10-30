"use client";

import { Fragment, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { api, fmtDate } from "@/lib/api";
import { useMe } from "@/lib/useMe";

type Usage = {
  tokens: number; input_tokens: number; output_tokens: number; split_complete: boolean;
  cost_usd: number; cost_complete: boolean; llm_calls: number;
};
type Task = Usage & { task: string; provider: string | null; model: string | null };
type Revision = { revision: number | null; messages: number; attempts: number; tokens: number; tasks: Task[] };
type Project = Usage & { id: string; status: string; created_at: string; messages: number; revisions: Revision[] };
type Data = {
  user: { id: string; name: string; email: string | null; status: string };
  summary: Usage & { messages: number; projects: number; reels: number };
  unattributed: Usage & { messages: number };
  items: Project[]; total: number; limit: number; offset: number;
};
const num = (n: number) => n.toLocaleString();
const usd = (u: Usage) => `${u.cost_complete ? "" : "Known portion: "}$${u.cost_usd.toFixed(6)}`;
const split = (u: Usage, key: "input_tokens" | "output_tokens") => u.split_complete ? num(u[key]) : "Unavailable";
const LIMIT = 25;

export default function UserUsage() {
  const { me } = useMe({ required: true });
  const { userId } = useParams<{ userId: string }>();
  const [data, setData] = useState<Data | null>(null);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState("");
  useEffect(() => { setOffset(0); }, [userId]);
  useEffect(() => {
    if (me?.role !== "admin") return;
    let active = true;
    setData(null); setError("");
    api<Data>(`/api/admin/users/${encodeURIComponent(userId)}/usage?limit=${LIMIT}&offset=${offset}`)
      .then(value => { if (active) setData(value); })
      .catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [me, userId, offset]);
  if (!me) return <p className="muted">Loading…</p>;
  if (me.role !== "admin") return <div className="alert error">This area is for admins only.</div>;
  return <div className="stack">
    <Link href="/admin">← Users</Link>
    <h1 style={{ margin: 0 }}>User usage{data ? `: ${data.user.name || "Unknown"}` : ""}</h1>
    <p className="muted">Usage metadata only. Project references contain no titles or private content. All totals include recorded preparation, revisions, and retries.</p>
    {error && <div className="alert error" role="alert">{error}</div>}
    {!data && !error && <p role="status">Loading usage…</p>}
    {data && <>
      <div className="grid grid-3">
        <div className="card"><div className="small muted">User messages · all time</div><h2>{num(data.summary.messages)}</h2><div>{num(data.summary.reels)} completed reels / {num(data.summary.projects)} projects</div></div>
        <div className="card"><div className="small muted">AI tokens · all time</div><h2>{num(data.summary.tokens)}</h2><div>{num(data.summary.llm_calls)} recorded model calls</div></div>
        <div className="card"><div className="small muted">Estimated provider cost · all time</div><h2>{usd(data.summary)}</h2><div className="small muted">Historical estimates retained; unknown model prices excluded.</div></div>
      </div>
      <div className="card">
        <h2>Projects</h2>
        <div className="table-wrap"><table>
          <thead><tr><th>Project reference / revisions</th><th>Created</th><th>Status</th><th>User messages</th><th>Input tokens</th><th>Output tokens</th><th>Total tokens</th><th>Est. cost</th></tr></thead>
          <tbody>{data.items.map(p => <Fragment key={p.id}>
            <tr><td><code title={p.id}>{p.id.slice(0, 8)}</code></td><td>{fmtDate(p.created_at)}</td><td>{p.status}</td><td>{num(p.messages)}</td><td>{split(p, "input_tokens")}</td><td>{split(p, "output_tokens")}</td><td>{num(p.tokens)}</td><td>{usd(p)}</td></tr>
            <tr><td colSpan={8}><details><summary>Revision breakdown for {p.id.slice(0, 8)}</summary>
              {p.revisions.map(r => <div key={r.revision ?? "unknown"} style={{ margin: "16px 0" }}>
                <strong>{r.revision === null ? "Unattributed revision" : `Revision ${r.revision}`}</strong>
                <p className="small muted">{num(r.messages)} user messages · {num(r.tokens)} tokens · {num(r.tasks.reduce((n, t) => n + t.llm_calls, 0))} recorded model calls, including retries</p>
                {r.tasks.length ? <table><thead><tr><th>Task</th><th>Provider / model</th><th>Model calls</th><th>Input</th><th>Output</th><th>Total tokens</th><th>Est. cost</th></tr></thead><tbody>
                  {r.tasks.map((t, i) => <tr key={i}><td>{t.task}</td><td>{t.provider || "Unknown"} / {t.model || "Unknown"}</td><td>{num(t.llm_calls)}</td><td>{split(t, "input_tokens")}</td><td>{split(t, "output_tokens")}</td><td>{num(t.tokens)}</td><td>{usd(t)}</td></tr>)}
                </tbody></table> : <p className="small muted">No recorded model usage for this revision.</p>}
              </div>)}
            </details></td></tr>
          </Fragment>)}
          {!data.items.length && <tr><td colSpan={8}>No projects yet.</td></tr>}</tbody>
        </table></div>
        <div className="row spread" style={{ marginTop: 12 }}>
          <span>{data.total ? `${offset + 1}–${Math.min(offset + LIMIT, data.total)} of ${data.total}` : "0 projects"}</span>
          <div className="row"><button className="btn secondary small" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - LIMIT))}>Previous</button><button className="btn secondary small" disabled={offset + LIMIT >= data.total} onClick={() => setOffset(offset + LIMIT)}>Next</button></div>
        </div>
      </div>
      <div className="card"><h2>Other / unattributed usage</h2>
        <p>{num(data.unattributed.messages)} user messages · {num(data.unattributed.tokens)} tokens · {usd(data.unattributed)} estimated cost</p>
        <p className="small muted">Includes general conversations and historical records without a reliable project link. Historical token splits and revision attribution may be unavailable. Totals reflect recorded provider usage.</p>
      </div>
    </>}
  </div>;
}
