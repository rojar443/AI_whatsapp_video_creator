"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import TelegramCard from "@/components/TelegramCard";
import WhatsAppCard from "@/components/WhatsAppCard";
import { api, fmtDate, ProjectSummary, STATUS_LABEL } from "@/lib/api";
import { useMe } from "@/lib/useMe";

export default function Dashboard() {
  const { me, refresh } = useMe({ required: true });
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);

  useEffect(() => {
    if (!me) return;
    const load = () => api<{ items: ProjectSummary[] }>("/api/projects").then((r) => setProjects(r.items)).catch(() => {});
    load();
    const t = setInterval(load, 5000); // live status while videos generate
    return () => clearInterval(t);
  }, [me]);

  if (!me) return <p className="muted">Loading…</p>;

  return (
    <div className="stack">
      <div className="row spread">
        <h1 style={{ margin: 0 }}>Hi {me.name.split(" ")[0]}</h1>
        <span className="badge ok">{me.credits} credits</span>
      </div>
      <div className="grid grid-2">
        <WhatsAppCard me={me} onChange={refresh} />
        <TelegramCard me={me} onChange={refresh} />
        <div className="card">
          <h3>Quick tips</h3>
          <ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>
            <li>Send 3–15 clips or photos, then say <em>"that's all"</em></li>
            <li>Describe the vibe: <em>"cinematic travel reel, calm voiceover"</em> (text or voice note)</li>
            <li>After delivery: <em>"shorter"</em>, <em>"start with the beach"</em>, <em>"no music"</em></li>
            <li>Reply <em>status</em>, <em>link</em>, <em>new</em> or <em>help</em> any time</li>
          </ul>
        </div>
      </div>

      <h2 style={{ marginTop: 28 }}>Your reels</h2>
      {projects === null ? (
        <p className="muted">Loading…</p>
      ) : projects.length === 0 ? (
        <div className="card muted">No reels yet. Send clips and photos to ReelChat on WhatsApp!</div>
      ) : (
        <div className="grid grid-2">
          {projects.map((p) => (
            <Link key={p.id} href={`/dashboard/projects/${p.id}`} className="card project-card" style={{ color: "inherit" }}>
              {p.video?.url ? (
                <video className="video-thumb" src={p.video.url + "&inline=1"} muted preload="metadata" />
              ) : (
                <div className="ph">{STATUS_LABEL[p.status] ?? p.status}</div>
              )}
              <div>
                <div className="row spread">
                  <strong>{p.title}</strong>
                  <span className={`badge ${p.status}`}>{STATUS_LABEL[p.status] ?? p.status}</span>
                </div>
                <p className="small muted" style={{ margin: "6px 0" }}>
                  {p.sources} uploads · revision {p.revision} · {fmtDate(p.updated_at)}
                </p>
                {p.last_error && <p className="small" style={{ color: "var(--danger)", margin: 0 }}>{p.last_error}</p>}
                {p.video?.is_demo && <span className="badge pending">Demo output</span>}
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
