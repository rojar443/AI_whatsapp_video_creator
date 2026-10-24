import Link from "next/link";
import RegisterLink from "@/components/RegisterLink";
import StartButton from "@/components/StartButton";

const demo: [string, "in" | "out"][] = [
  ["🎞️ beach.mp4 · hike.mp4 · old-town.mp4", "out"],
  ["I received 3 clips. Send more, or tell me what you want.", "in"],
  ["I want a travel reel - cinematic adventure from these videos", "out"],
  ["Plan: ~30 s 9:16 reel · opening on the best landscape shot · 5 strongest moments · cinematic grade · calm narration · soft music. Proceed?", "in"],
  ["Yes", "out"],
  ["Your reel is ready! 🎬 Want any changes?", "in"],
  ["Make the opening faster and end on the sunset", "out"],
];

export default function Home() {
  return (
    <>
      <section className="hero">
        <div>
          <h1>
            Turn your clips into <span className="pop">Reels</span> by chatting on Telegram
          </h1>
          <p className="lead">
            Send the photos and videos sitting on your phone to our Telegram bot, say the vibe you want, approve the
            plan, and get a finished vertical reel for Instagram or YouTube Shorts in the same chat. No editing skills.
          </p>
          <div className="row">
            <StartButton />
            <RegisterLink />
          </div>
          <p className="small muted" style={{ marginTop: 12 }}>
            No account needed — just message the bot.
          </p>
        </div>
        <div className="phone" aria-label="Example Telegram conversation">
          <div className="chat">
            {demo.map(([t, d], i) => (
              <div key={i} className={`bubble ${d}`}>{t}</div>
            ))}
          </div>
        </div>
      </section>

      <section id="how" style={{ paddingTop: 24 }}>
        <h2>How it works</h2>
        <div className="grid grid-2" style={{ marginTop: 16 }}>
          <div className="card">
            <ol className="steps">
              <li><strong>Send your clips and photos</strong> to the bot in Telegram.</li>
              <li><strong>Say what you want</strong> — "cinematic travel reel, calm voiceover" — by text or voice note.</li>
              <li><strong>Check the plan.</strong> See which moments were picked and why. Nothing is made until you say yes.</li>
              <li><strong>Get your reel</strong> in the chat, then ask for changes like "shorter" or "start with the beach".</li>
            </ol>
          </div>
          <div className="grid grid-2">
            {[
              ["Best moments, picked for you", "Dark, shaky and duplicate shots are dropped; the strongest moments are arranged hook → highlights → payoff."],
              ["Genre playbooks", "Travel and daily-life reels follow curated editing rules for pacing, structure and look."],
              ["Narration & captions", "A short AI-written script, read by a natural voice. Captions come from the script, so silent clips work."],
              ["Photos come alive", "Photos get a slow zoom or pan so they sit naturally between your clips."],
              ["Made for sharing", "9:16 MP4 sized to send in the chat, or a secure download link."],
              ["Fair credits", "Failed edits are refunded automatically. No double charges."],
            ].map(([t, d]) => (
              <div className="card" key={t}>
                <h3>{t}</h3>
                <p className="muted small" style={{ margin: 0 }}>{d}</p>
              </div>
            ))}
          </div>
        </div>
      </section>
    </>
  );
}
