"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { api, Me } from "@/lib/api";

type Msg = {
  id: string; direction: "inbound" | "outbound"; kind: string; text: string | null; transcript: string | null;
  status: string; error: string | null; at: string; buttons?: { id: string; title: string }[];
  video_url?: string; is_demo?: boolean; via_template?: boolean;
};

export default function Simulator() {
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const [phone, setPhone] = useState("+15557654321");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [panel, setPanel] = useState<"" | "media" | "voice">("");
  const [devOk, setDevOk] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const count = useRef(0);

  useEffect(() => {
    api<Me>("/api/me").then(setMe).catch(() => setMe(null));
  }, []);

  const q = me?.phone ? "" : `?phone=${encodeURIComponent(phone)}`;
  const load = useCallback(async () => {
    try {
      const r = await api<{ items: Msg[] }>(`/api/dev/whatsapp/messages${q}`);
      setMsgs(r.items);
      setDevOk(true);
    } catch (e: any) {
      if (e.status === 404) setDevOk(false);
    }
  }, [q]);

  useEffect(() => {
    if (me === undefined) return;
    load();
    const t = setInterval(load, 1500);
    return () => clearInterval(t);
  }, [me, load]);

  useEffect(() => {
    if (msgs.length !== count.current && box.current) box.current.scrollTop = box.current.scrollHeight;
    count.current = msgs.length;
  }, [msgs]);

  async function call(fn: () => Promise<unknown>) {
    setError("");
    setBusy(true);
    try {
      await fn();
      load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const send = (body: { text?: string; button_id?: string; button_title?: string }) =>
    call(() => api("/api/dev/whatsapp/send", { method: "POST", json: { ...body, phone: me?.phone ? undefined : phone } }));

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    const t = text;
    setText("");
    send({ text: t });
  }

  function upload(e: FormEvent<HTMLFormElement>, path: string) {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    if (!me?.phone) fd.set("phone", phone);
    call(() => api(path, { method: "POST", body: fd })).then(() => setPanel(""));
  }

  if (me === undefined) return <p className="muted">Loading…</p>;
  if (devOk === false) return <div className="alert error">The simulator is only available in development mode.</div>;

  return (
    <div className="grid" style={{ gridTemplateColumns: "minmax(0, 520px)", justifyContent: "center" }}>
      <div>
        <h1 style={{ fontSize: "1.5rem" }}>WhatsApp simulator</h1>
        <p className="small muted">
          Messages go through the real webhook pipeline (signed payload → worker → agent) — dev only, nothing reaches
          WhatsApp. {me?.phone ? <>Sending as <strong>{me.phone}</strong>.</> : "You're an anonymous chat user (no account number): pick a test number below."}
        </p>
        {!me?.phone && (
          <div className="field">
            <label htmlFor="ph">Test phone number</label>
            <input id="ph" value={phone} onChange={(e) => setPhone(e.target.value)} />
          </div>
        )}
        {error && <div className="alert error">{error}</div>}
        <div className="sim" ref={box} aria-live="polite">
          <div className="chat">
            {msgs.map((m) => (
              <div key={m.id} className={`bubble ${m.direction === "inbound" ? "out" : "in"}`}>
                {m.kind === "audio" && <div className="small muted">🎤 Voice note{m.transcript ? `: "${m.transcript}"` : ""}</div>}
                {m.kind === "video" && m.direction === "inbound" && <div>🎞 Video clip</div>}
                {m.kind === "image" && m.direction === "inbound" && <div>📷 Photo</div>}
                {m.video_url && <video src={m.video_url} controls playsInline preload="metadata" />}
                {m.kind !== "audio" && m.text}
                {m.buttons && m.buttons.length > 0 && (
                  <div className="bubble-buttons">
                    {m.buttons.map((b) => (
                      <button key={b.id} disabled={busy} onClick={() => send({ button_id: b.id, button_title: b.title })}>
                        {b.title}
                      </button>
                    ))}
                  </div>
                )}
                <div className="meta">
                  {new Date(m.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                  {m.via_template && " · template"}
                  {m.direction === "outbound" && ` · ${m.status}`}
                  {m.error && ` · ${m.error}`}
                </div>
              </div>
            ))}
            {msgs.length === 0 && <p className="muted small" style={{ textAlign: "center" }}>Send some clips to start 👋</p>}
          </div>
        </div>
        <form className="sim-input" onSubmit={submit}>
          <button type="button" className="btn secondary" title="Attach clips or photos" onClick={() => setPanel(panel === "media" ? "" : "media")}>📎</button>
          <button type="button" className="btn secondary" title="Voice note" onClick={() => setPanel(panel === "voice" ? "" : "voice")}>🎤</button>
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Type a message" aria-label="Message" />
          <button className="btn" disabled={busy}>Send</button>
        </form>
        {panel === "media" && (
          <form className="card stack" onSubmit={(e) => upload(e, "/api/dev/whatsapp/media")} style={{ marginTop: 10 }}>
            <div>
              <label htmlFor="mf">Videos or photos from your computer</label>
              <input id="mf" name="files" type="file" accept="video/*,image/*" multiple required />
            </div>
            <div>
              <label htmlFor="mc">Caption (optional)</label>
              <input id="mc" name="caption" placeholder="cinematic travel reel, that's all" />
            </div>
            <div className="row">
              <button className="btn" disabled={busy}>Send files</button>
              <button type="button" className="btn secondary" disabled={busy}
                      onClick={() => call(() => api(`/api/dev/whatsapp/samples${q}`, { method: "POST" })).then(() => setPanel(""))}>
                Send sample footage
              </button>
            </div>
            <p className="small muted" style={{ margin: 0 }}>Sample footage is synthetic (labelled SAMPLE) — use real phone clips for real tests.</p>
          </form>
        )}
        {panel === "voice" && (
          <form className="card stack" onSubmit={(e) => upload(e, "/api/dev/whatsapp/voice")} style={{ marginTop: 10 }}>
            <div>
              <label htmlFor="vf">Audio file (.ogg, .mp3, .m4a, .wav)</label>
              <input id="vf" name="file" type="file" accept="audio/*" required />
            </div>
            <div>
              <label htmlFor="vt">What it says (used only by the mock transcriber)</label>
              <input id="vt" name="transcript" placeholder="That's all. Make it a calm daily life reel." />
            </div>
            <button className="btn" disabled={busy}>Send voice note</button>
          </form>
        )}
      </div>
    </div>
  );
}
