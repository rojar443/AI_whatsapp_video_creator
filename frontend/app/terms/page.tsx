export const metadata = { title: "Terms · ReelChat" };

export default function Terms() {
  return (
    <article className="prose">
      <h1>Terms of Service</h1>
      <p className="muted">Template for the MVP — have it reviewed by counsel before launch.</p>
      <h2>The service</h2>
      <p>
        ReelChat creates short vertical videos from your instructions using automated and AI-based tools. Output may
        contain mistakes; review videos before publishing them.
      </p>
      <h2>Credits</h2>
      <p>
        Each generation costs credits, shown before you confirm. Credits for failed or cancelled-before-start
        generations are refunded automatically. Credits have no cash value.
      </p>
      <h2>Acceptable use</h2>
      <p>
        Don't request illegal, hateful, sexual, violent or deceptive content, content that infringes others' rights,
        or impersonation of real people. We may refuse requests and suspend accounts that misuse the service.
      </p>
      <h2>Your content</h2>
      <p>
        You keep the rights to your instructions. Subject to third-party licences (e.g. stock footage and music
        providers), you may use generated videos for personal and commercial purposes.
      </p>
      <h2>Availability</h2>
      <p>The service is provided as is, without guarantees of uptime or fitness for a particular purpose.</p>
    </article>
  );
}
