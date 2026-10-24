"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, fmtDate, mmss, ProjectDetail, STATUS_LABEL } from "@/lib/api";
import { useMe } from "@/lib/useMe";

const ROLE: Record<string, string> = { hook: "Opening", build: "", payoff: "Ending" };
const FIELDS: [string, string][] = [
  ["genre", "Genre"], ["vibe", "Vibe"], ["format", "Format"], ["duration", "Length"], ["style", "Look"],
  ["quality", "Quality"], ["narration", "Narration"], ["captions", "Captions"], ["music", "Audio"],
  ["title_text", "Title"], ["instructions", "Your instructions"],
];

export default function ProjectPage() {
  const { id } = useParams<{ id: string }>();
  const { me } = useMe({ required: true });
  const [p, setP] = useState<ProjectDetail | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api<ProjectDetail>(`/api/projects/${id}`).then(setP).catch((e) => setError(e.message));
  }, [id]);

  useEffect(() => {
    if (!me) return;
    load();
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [me, load]);

  async function retry() {
    setBusy(true);
    try {
      await api(`/api/projects/${id}/retry`, { method: "POST" });
      load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  if (error && !p) return <div className="alert error">{error}</div>;
  if (!p) return <p className="muted">Loading…</p>;
  const current = p.preferences[p.preferences.length - 1];
  const plan = current?.plan;
  const videos = p.assets.filter((a) => a.kind === "video");

  return (
    <div className="stack">
      <Link href="/dashboard" className="small">← All reels</Link>
      <div className="row spread">
        <h1 style={{ margin: 0 }}>{p.title}</h1>
        <span className={`badge ${p.status}`}>{STATUS_LABEL[p.status] ?? p.status}</span>
      </div>
      {error && <div className="alert error">{error}</div>}
      {p.status === "failed" && (
        <div className="alert error row spread">
          <span>Editing failed: {p.last_error}. Credits were refunded.</span>
          {p.can_retry && <button className="btn small" disabled={busy} onClick={retry}>Retry</button>}
        </div>
      )}

      <div className="grid grid-2">
        <div className="card">
          <h3>Reels</h3>
          {videos.length === 0 && <p className="muted small">No reel yet — continue in WhatsApp.</p>}
          <div className="grid grid-2">
            {videos.slice().reverse().map((v) => (
              <div key={v.id} className="stack">
                {v.deleted || !v.url ? (
                  <div className="ph muted small">Expired</div>
                ) : (
                  <video className="video-thumb" src={v.url + "&inline=1"} controls playsInline preload="metadata" />
                )}
                <div className="small muted">
                  {v.width}×{v.height} · {Math.round(v.duration_seconds ?? 0)} s · {(v.size_bytes / 1e6).toFixed(1)} MB
                </div>
                {v.is_demo && <span className="badge pending" title={(v.demo || []).join(", ")}>Demo mode output</span>}
                {v.url && <a className="btn secondary small" href={v.url}>Download</a>}
              </div>
            ))}
          </div>
        </div>

        <div className="card">
          <h3>Settings · revision {current?.revision ?? 1}</h3>
          {current ? (
            <dl className="kv">
              {FIELDS.map(([k, label]) => {
                const v = current.values[k];
                if (v === null || v === undefined || v === "") return null;
                const src = current.sources[k];
                return (
                  <div key={k} style={{ display: "contents" }}>
                    <dt>{label}</dt>
                    <dd>
                      {typeof v === "boolean" ? (v ? "On" : "Off") : String(v).replace(/_/g, " ")}
                      {src && src !== "user" && <span className="muted small"> · suggested</span>}
                    </dd>
                  </div>
                );
              })}
            </dl>
          ) : <p className="muted small">Still collecting details on WhatsApp.</p>}
          {current?.playbook && <p className="small muted" style={{ marginTop: 10 }}>Playbook: {current.playbook}</p>}
        </div>
      </div>

      {plan && (
        <div className="card">
          <h3>Plan · ~{Math.round(plan.total_seconds)} s</h3>
          <ol className="small" style={{ paddingLeft: 18, margin: 0 }}>
            {plan.items.map((it, i) => (
              <li key={i} style={{ marginBottom: 4 }}>
                {ROLE[it.role] && <strong>{ROLE[it.role]} — </strong>}
                {plan.labels?.[it.asset_id] ?? "clip"}
                {it.kind !== "photo" && ` ${mmss(it.source_start)}–${mmss(it.source_end)}`}
                {(it.description || it.reason) && <span className="muted">: {it.description || it.reason}</span>}
              </li>
            ))}
          </ol>
          {plan.dropped?.length > 0 && (
            <p className="small muted" style={{ marginTop: 10 }}>
              Skipped: {plan.dropped.map((d) => `${d.label} (${d.reason})`).join(" · ")}
            </p>
          )}
          {plan.script && plan.script.lines.length > 0 && (
            <p className="small" style={{ marginTop: 10 }}>
              <strong>Script:</strong> {plan.script.lines.map((l) => l.text).join(" ")}
            </p>
          )}
          <p className="small muted">Moment scoring: {plan.scorer}{plan.music_title ? ` · Music: ${plan.music_title}` : ""}</p>
        </div>
      )}

      <div className="grid grid-2">
        <div className="card">
          <h3>Your uploads ({p.source_files.length})</h3>
          <ul className="small" style={{ paddingLeft: 18, margin: 0 }}>
            {p.source_files.map((f) => (
              <li key={f.id}>
                {f.label}{f.duration_seconds ? ` · ${f.duration_seconds.toFixed(1)} s` : ""}{" "}
                {f.status !== "ready" && <span className={`badge ${f.status === "rejected" ? "failed" : ""}`}>{f.status}</span>}
                {f.reject_reason && <span className="muted"> — {f.reject_reason}</span>}
              </li>
            ))}
          </ul>
          <p className="small muted">Source files are deleted automatically after the retention period.</p>
        </div>
        <div className="card table-wrap">
          <h3>Processing history</h3>
          <table>
            <thead><tr><th>Rev</th><th>Run</th><th>Status</th><th>Stage</th><th>Credits</th><th>Started</th></tr></thead>
            <tbody>
              {p.jobs.map((j) => (
                <tr key={j.id} title={j.pipeline ? Object.entries(j.pipeline).map(([k, v]) => `${k}: ${v}`).join("\n") : ""}>
                  <td>{j.revision}</td><td>{j.run_number}</td>
                  <td><span className={`badge ${j.status}`}>{j.status}</span>{j.error_message && <div className="small">{j.error_message}</div>}</td>
                  <td>{j.stage ?? "—"}</td><td>{j.credits_charged}</td><td className="small">{fmtDate(j.created_at)}</td>
                </tr>
              ))}
              {p.jobs.length === 0 && <tr><td colSpan={6} className="muted">Not started yet.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
