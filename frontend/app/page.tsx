import Link from "next/link";

const demo: [string, "in" | "out"][] = [
  ["Create a motivational Reel about fitness", "out"],
  ["Nice — fitness. Where will you post it, and how long should it be?", "in"],
  ["Instagram, 30 seconds", "out"],
  ["Here's your video plan 🎬\n• Vibe: Motivational\n• Style: Cinematic (suggested)\n• Length: 30 s\nCost: 2 credits. Reply yes to generate.", "in"],
  ["yes", "out"],
  ["✅ Your video is ready!", "in"],
  ["Make it more cinematic, faster pace", "out"],
];

export default function Home() {
  return (
    <>
      <section className="hero">
        <div>
          <h1>Make Reels and Shorts by chatting on WhatsApp</h1>
          <p className="lead">
            Describe your idea in a text or a voice note. ReelChat asks a couple of quick questions, shows you a plan,
            and sends the finished vertical video back to the same chat.
          </p>
          <div className="row">
            <Link className="btn" href="/register">Create free account</Link>
            <Link className="btn secondary" href="#how">How it works</Link>
          </div>
          <p className="small muted" style={{ marginTop: 12 }}>10 free credits · no editing skills needed</p>
        </div>
        <div className="phone" aria-label="Example WhatsApp conversation">
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
              <li><strong>Sign up</strong> with your email and WhatsApp number.</li>
              <li><strong>Link WhatsApp</strong> by sending the 6-digit code we show you.</li>
              <li><strong>Chat your idea</strong> — topic, vibe, length — by text or voice note.</li>
              <li><strong>Approve the plan.</strong> Nothing is generated (or charged) until you say yes.</li>
              <li><strong>Get your video</strong> in the chat, and ask for changes any time.</li>
            </ol>
          </div>
          <div className="grid grid-2">
            {[
              ["Conversational", "No long forms. Sensible defaults, and only the questions that matter."],
              ["Voice notes", "Speak in English, Hindi, Malayalam and more."],
              ["Made for vertical", "9:16 MP4 for Instagram Reels, YouTube Shorts and more."],
              ["Revisions", "\"Make it more cinematic\", \"use a faster pace\" — just ask."],
              ["Your history", "Every project, revision and download on your dashboard."],
              ["Fair credits", "Failed videos are refunded automatically. No double charges."],
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
