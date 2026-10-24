"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, fmtDate, ProjectDetail, STATUS_LABEL } from "@/lib/api";
import { useMe } from "@/lib/useMe";

const FIELD_LABELS: Record<string, string> = {
  topic: "Topic", vibe: "Vibe", format: "Format", aspect_ratio: "Aspect ratio", duration_seconds: "Length (s)",
  visual_style: "Style", quality: "Quality", language: "Language", voice: "Voice", captions_enabled: "Captions",
  caption_language: "Caption language", music: "Music", pace: "Pace", call_to_action: "Call to action",
  brand_colors: "Brand colours", script_mode: "Script",
};

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
  const videos = p.assets.filter((a) => a.kind === "video");

  return (
    <div className="stack">
      <Link href="/dashboard" className="small">← All videos</Link>
      <div className="row spread">
        <h1 style={{ margin: 0 }}>{p.title}</h1>
        <span className={`badge ${p.status}`}>{STATUS_LABEL[p.status] ?? p.status}</span>
      </div>
      {error && <div className="alert error">{error}</div>}
      {p.status === "failed" && (
        <div className="alert error row spread">
          <span>Generation failed: {p.last_error}. Credits were refunded.</span>
          {p.can_retry && <button className="btn small" disabled={busy} onClick={retry}>Retry</button>}
        </div>
      )}

      <div className="grid grid-2">
        <div className="card">
          <h3>Videos</h3>
          {videos.length === 0 && <p className="muted small">No video yet.</p>}
          <div className="grid grid-2">
            {videos.slice().reverse().map((v) => (
              <div key={v.id} className="stack">
                {v.deleted || !v.url ? (
                  <div className="ph muted small">Expired</div>
                ) : (
                  <video className="video-thumb" src={v.url + "&inline=1"} controls playsInline preload="metadata" />
                )}
                <div className="small muted">
                  {v.width}×{v.height} · {Math.round(v.duration_seconds ?? 0)} s · {fmtDate(v.created_at)}
                </div>
                {v.is_mock && <span className="badge pending">Mock preview — not AI-generated</span>}
                {v.url && <a className="btn secondary small" href={v.url}>Download</a>}
              </div>
            ))}
          </div>
        </div>

        <div className="card">
          <h3>Settings (revision {current?.revision ?? 1})</h3>
          {current ? (
            <dl className="kv">
              {Object.entries(FIELD_LABELS).map(([k, label]) => {
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
          ) : (
            <p className="muted small">Still collecting details on WhatsApp.</p>
          )}
          {current && <p className="small muted" style={{ marginTop: 12 }}>Cost: {current.credits_cost} credits</p>}
        </div>
      </div>

      <div className="card">
        <h3>Generation history</h3>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Revision</th><th>Run</th><th>Status</th><th>Stage</th><th>Credits</th><th>Engine</th><th>Started</th><th>Error</th></tr>
            </thead>
            <tbody>
              {p.jobs.map((j) => (
                <tr key={j.id}>
                  <td>{j.revision}</td>
                  <td>{j.run_number}</td>
                  <td><span className={`badge ${j.status}`}>{j.status}</span></td>
                  <td>{j.stage ?? "—"}</td>
                  <td>{j.credits_charged}</td>
                  <td>{j.provider}{j.is_mock ? " (mock)" : ""}</td>
                  <td>{fmtDate(j.created_at)}</td>
                  <td className="small">{j.error_message ?? ""}</td>
                </tr>
              ))}
              {p.jobs.length === 0 && <tr><td colSpan={8} className="muted">Not generated yet.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
