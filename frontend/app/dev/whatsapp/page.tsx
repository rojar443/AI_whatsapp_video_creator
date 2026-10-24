"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useMe } from "@/lib/useMe";

type Msg = {
  id: string; direction: "inbound" | "outbound"; kind: string; text: string | null; transcript: string | null;
  status: string; error: string | null; at: string; buttons?: { id: string; title: string }[];
  video_url?: string; is_mock?: boolean; via_template?: boolean;
};

export default function Simulator() {
  const { me } = useMe({ required: true });
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [voiceOpen, setVoiceOpen] = useState(false);
  const [devOk, setDevOk] = useState<boolean | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const count = useRef(0);

  const load = useCallback(async () => {
    try {
      const r = await api<{ items: Msg[] }>("/api/dev/whatsapp/messages");
      setMsgs(r.items);
      setDevOk(true);
    } catch (e: any) {
      if (e.status === 404) setDevOk(false);
    }
  }, []);

  useEffect(() => {
    if (!me) return;
    load();
    const t = setInterval(load, 1500);
    return () => clearInterval(t);
  }, [me, load]);

  useEffect(() => {
    if (msgs.length !== count.current && box.current) box.current.scrollTop = box.current.scrollHeight;
    count.current = msgs.length;
  }, [msgs]);

  async function send(body: { text?: string; button_id?: string; button_title?: string }) {
    setError("");
    try {
      await api("/api/dev/whatsapp/send", { method: "POST", json: body });
      load();
    } catch (e: any) {
      setError(e.message);
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    const t = text;
    setText("");
    await send({ text: t });
  }

  async function sendVoice(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    setError("");
    try {
      await api("/api/dev/whatsapp/voice", { method: "POST", body: fd });
      setVoiceOpen(false);
      load();
    } catch (err: any) {
      setError(err.message);
    }
  }

  if (!me) return <p className="muted">Loading…</p>;
  if (devOk === false) return <div className="alert error">The simulator is only available in development mode.</div>;

  return (
    <div className="grid" style={{ gridTemplateColumns: "minmax(0, 520px)", justifyContent: "center" }}>
      <div>
        <h1 style={{ fontSize: "1.5rem" }}>WhatsApp simulator</h1>
        <p className="small muted">
          Messages are sent as <strong>{me.phone}</strong> through the real webhook pipeline (signed payload → worker →
          agent). Replies appear below. Dev only — no messages reach WhatsApp.
        </p>
        {me.whatsapp.status !== "linked" && (
          <div className="alert warn">Not linked yet: get a code on the dashboard, then send <code>LINK 123456</code> here.</div>
        )}
        {error && <div className="alert error">{error}</div>}
        <div className="sim" ref={box} aria-live="polite">
          <div className="chat">
            {msgs.map((m) => (
              <div key={m.id} className={`bubble ${m.direction === "inbound" ? "out" : "in"}`}>
                {m.kind === "audio" && <div className="small muted">🎤 Voice note{m.transcript ? `: "${m.transcript}"` : ""}</div>}
                {m.video_url && (
                  <video src={m.video_url} controls playsInline preload="metadata" />
                )}
                {m.kind !== "audio" && m.text}
                {m.kind === "link" && m.video_url && <div className="small"><a href={m.video_url}>Download link</a></div>}
                {m.buttons && m.buttons.length > 0 && (
                  <div className="bubble-buttons">
                    {m.buttons.map((b) => (
                      <button key={b.id} onClick={() => send({ button_id: b.id, button_title: b.title })}>{b.title}</button>
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
            {msgs.length === 0 && <p className="muted small" style={{ textAlign: "center" }}>Say hi 👋</p>}
          </div>
        </div>
        <form className="sim-input" onSubmit={submit}>
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Type a message" aria-label="Message" />
          <button type="button" className="btn secondary" onClick={() => setVoiceOpen(!voiceOpen)} title="Send a voice note">🎤</button>
          <button className="btn">Send</button>
        </form>
        {voiceOpen && (
          <form className="card stack" onSubmit={sendVoice} style={{ marginTop: 10 }}>
            <div>
              <label htmlFor="vf">Audio file (.ogg, .mp3, .m4a, .wav)</label>
              <input id="vf" name="file" type="file" accept="audio/*" required />
            </div>
            <div>
              <label htmlFor="vt">What the voice note says</label>
              <input id="vt" name="transcript" placeholder="Make a funny 15 second reel about my cat" />
              <p className="small muted" style={{ margin: "4px 0 0" }}>
                Used by the mock transcriber. With a real speech-to-text provider this is ignored.
              </p>
            </div>
            <button className="btn">Send voice note</button>
          </form>
        )}
      </div>
    </div>
  );
}
