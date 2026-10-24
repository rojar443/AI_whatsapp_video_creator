import Link from "next/link";

const demo: [string, "in" | "out"][] = [
  ["🎞️ beach.mp4 · hike.mp4 · old-town.mp4", "out"],
  ["I want to make a travel reel - cinematic adventure from these videos", "out"],
  ["Love these clips! Where will you post it, and how long should it be? I'll pick the best moments.", "in"],
  ["Instagram, 30 seconds", "out"],
  ["Here's your reel plan 🎬\n• Vibe: Cinematic adventure\n• Clips: 3 uploaded (about 2 min)\n• Length: 30 s · epic music\nCost: 2 credits. Reply yes to edit.", "in"],
  ["yes", "out"],
  ["✅ Your reel is ready!", "in"],
  ["Make the opening faster and end on the sunset", "out"],
];

export default function Home() {
  return (
    <>
      <section className="hero">
        <div>
          <h1>Make Reels and Shorts by chatting on WhatsApp</h1>
          <p className="lead">
            Send your clips and tell us the story you want - a travel adventure, a product launch, a family highlight.
            ReelChat asks a couple of quick questions, shows you a plan, and sends the finished vertical reel back to the same chat.
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
              <li><strong>Send your videos</strong> and describe the reel you want - vibe, length, platform - by text or voice note.</li>
              <li><strong>Approve the plan.</strong> Nothing is generated (or charged) until you say yes.</li>
              <li><strong>Get your reel</strong> in the chat, and ask for changes any time.</li>
            </ol>
          </div>
          <div className="grid grid-2">
            {[
              ["Your footage, edited for you", "Upload your clips; we find the best moments, cut them to music and format for vertical."],
              ["Voice notes", "Speak in English, Hindi, Malayalam and more."],
              ["Made for vertical", "9:16 MP4 for Instagram Reels, YouTube Shorts and more."],
              ["Revisions", "\"Make it more cinematic\", \"start with the sunset\" — just ask."],
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
