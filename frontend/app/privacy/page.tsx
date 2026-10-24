export const metadata = { title: "Privacy · ReelChat" };

export default function Privacy() {
  return (
    <article className="prose">
      <h1>Privacy Policy</h1>
      <p className="muted">Template for the MVP — have it reviewed by counsel before launch.</p>

      <h2>What we collect</h2>
      <ul>
        <li>Account data: name, email, optional phone number, hashed password.</li>
        <li>The photos and video clips you send, the chat messages you send to ReelChat, and transcripts of your
          voice notes.</li>
        <li>Reel projects: your preferences, the editing plan, generated scripts, finished reels and their status.</li>
        <li>Usage records (credits, generation counts) and security logs (sign-ins, account changes).</li>
      </ul>

      <h2>How we use AI services</h2>
      <p>
        To edit your reel, frames from your clips and photos, your messages and voice notes are sent to third-party AI
        providers we configure (currently Google Gemini to understand requests, pick moments and write the script,
        and ElevenLabs to read the narration). Voice notes are deleted right after transcription; we keep the
        transcript as part of the conversation.
      </p>

      <h2>Telegram</h2>
      <p>
        We only reply after you message our bot first, using Telegram's official Bot API. We see your Telegram name
        and chat id, not your phone number. Linking your chat to a website account is optional and moves your drafts
        into your history. Disconnect it on the Account page at any time. Telegram processes messages under its own
        privacy policy.
      </p>

      <h2>Retention</h2>
      <p>
        Your original clips and photos are deleted 7 days after upload. Finished reels are kept for 30 days and
        conversation messages for 90 days. If you haven't registered, drafts are deleted after 3 days. Project
        details stay in your history until you delete your account.
      </p>

      <h2>Your own voice (coming later)</h2>
      <p>
        If we offer narration in your own cloned voice, we will only create it after you confirm in WhatsApp that the
        sample is your own voice, we record that consent, and you can delete the voice at any time. Cloning another
        person's voice is never allowed.
      </p>

      <h2>Your rights</h2>
      <p>
        You can download your reels, unlink WhatsApp, and delete your account (which removes your messages,
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
