export const metadata = { title: "Privacy · ReelChat" };

export default function Privacy() {
  return (
    <article className="prose">
      <h1>Privacy Policy</h1>
      <p className="muted">Template for the MVP — have it reviewed by counsel before launch.</p>

      <h2>What we collect</h2>
      <ul>
        <li>Account data: name, email, WhatsApp number, hashed password.</li>
        <li>WhatsApp messages you send to ReelChat, and transcripts of your voice notes.</li>
        <li>Video projects: your preferences, generated scripts, videos and their status.</li>
        <li>Usage records (credits, generation counts) and security logs (sign-ins, admin actions).</li>
      </ul>

      <h2>How we use AI services</h2>
      <p>
        To understand your requests and create videos, your messages and voice notes are sent to third-party AI
        providers (for example a language model, a speech-to-text service, a text-to-speech service and a stock video
        or video generation service). Providers are configured by us and process data on our behalf. Voice recordings
        are deleted right after transcription; we keep the transcript as part of the conversation.
      </p>

      <h2>WhatsApp</h2>
      <p>
        We only message you after you link your number by sending us a code. We use the official WhatsApp Business
        Platform. Outside a 24-hour conversation window we only send approved notification templates about your
        videos. Unlink your number on the Account page to stop all messages.
      </p>

      <h2>Retention</h2>
      <p>
        Conversation messages are kept for 90 days and generated files for 30 days by default, after which they are
        deleted automatically. Project metadata stays in your history until you delete your account.
      </p>

      <h2>Your rights</h2>
      <p>
        You can download your videos, unlink WhatsApp, and delete your account (which removes your messages,
        projects and files) from the Account page. Contact us for any other request.
      </p>

      <h2>Security</h2>
      <p>
        Data is encrypted in transit. Download links are signed and expire. Access to your data is restricted to your
        account; administrators' actions are audited.
      </p>
    </article>
  );
}
