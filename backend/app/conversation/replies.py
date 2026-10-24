"""Reply templates. Kept short — this is WhatsApp."""
from typing import Any

from app.conversation.requirements import DURATIONS, label

TEMPLATES: dict[str, str] = {
    "welcome": (
        "Hi{name}! 👋 I turn your ideas into short vertical videos.\n"
        "Tell me what you'd like, e.g. *\"Create a motivational Reel about fitness\"*. "
        "You can type or send a voice note."
    ),
    "help": (
        "Here's what I can do:\n"
        "• Describe a video (text or voice) and I'll ask a couple of quick questions\n"
        "• Reply *yes* to the summary to start generating\n"
        "• After delivery, ask for changes like *\"make it more cinematic\"*\n"
        "• *status* – check progress · *new* – start over · *cancel* – stop · *link* – new download link"
    ),
    "ask_topic": "What should the video be about? (e.g. fitness tips, a travel spot, your product)",
    "ask_look": (
        "Nice — *{topic}*. What vibe and look are you going for?\n"
        "Vibe: motivational, funny, emotional, inspirational, educational\n"
        "Style: cinematic, realistic, animation, documentary, minimalist\n"
        "(or say *you choose*)"
    ),
    "ask_format": (
        "Where will you post it, and how long should it be?\n"
        "Format: Instagram Reel, YouTube Short, generic vertical\n"
        "Length: " + ", ".join(f"{d}s" for d in DURATIONS) + " · Quality: standard or high\n"
        "(or say *you choose*)"
    ),
    "not_linked": (
        "Hi! This number isn't linked to a ReelChat account yet.\n"
        "Sign up at {web_url}/register, then send the 6-digit code shown on your dashboard as "
        "*LINK 123456*."
    ),
    "linked": (
        "✅ Your WhatsApp is now linked to {name}'s ReelChat account.\n"
        "By using this chat you agree that your messages and voice notes are processed by AI "
        "services to create videos (see {web_url}/privacy). Reply *help* any time.\n\n"
        "What video would you like to make?"
    ),
    "link_failed": "That code didn't work or has expired. Generate a new one on your dashboard: {web_url}/dashboard",
    "login_confirmed": (
        "✅ You're signed in to ReelChat. Go back to the website - it will continue automatically.\n"
        "If this wasn't you, ignore this message and don't share codes with anyone."
    ),
    "login_failed": "That login code didn't work or has expired. Request a new one on {web_url}/login",
    "login_denied": "This account can't sign in right now. Contact support if you think this is a mistake.",
    "email_unverified": "Please verify your email address first: {web_url}/dashboard",
    "suspended": "Your account is suspended. Contact support if you think this is a mistake.",
    "rate_limited": "You're sending messages very quickly — please wait a minute and try again.",
    "queued": "🚀 Got it! Your video is queued. I'll message you when it's ready (usually a minute or two).",
    "processing": "🎬 Your video is being generated…",
    "already_generating": "This video is already being generated — I'll send it as soon as it's ready.",
    "busy_modify": "Your video is being generated right now. Once it's delivered you can ask for changes.",
    "insufficient_credits": (
        "You need {cost} credits for this video but have {balance}. "
        "Try a shorter or standard-quality video, or add credits on your dashboard: {web_url}/dashboard"
    ),
    "daily_limit": "You've reached today's limit of {limit} videos. Please try again tomorrow.",
    "cancelled": "Okay, I've cancelled that. Send a new idea whenever you're ready.",
    "cancelled_refund": "Okay, cancelled — {credits} credit(s) refunded.",
    "nothing_to_cancel": "There's nothing in progress to cancel.",
    "cannot_cancel_processing": "Generation has already started and can't be cancelled, sorry.",
    "status_none": "You have no video in progress. Tell me what you'd like to make!",
    "status": "Your video *{title}* is {status}.",
    "voice_failed": (
        "Sorry, I couldn't understand that voice note. Could you try again, or type your message?"
    ),
    "voice_too_large": "That voice note is too long. Please keep it under a couple of minutes.",
    "unsupported_media": "I can read text and voice notes. Please describe your video in words.",
    "unsupported": "Heads up: {items}.",
    "delivered_video": "✅ Your video is ready: *{title}*\nWant changes? e.g. \"make it more cinematic\" or \"use a faster pace\".",
    "delivered_link": "✅ Your video *{title}* is ready! Download (link expires in {hours} h):",
    "change_hint": "Want changes? e.g. \"make it more cinematic\" or \"use a faster pace\".",
    "mock_notice": "⚠️ Dev mode: this is a *mock preview* rendered locally, not AI-generated footage.",
    "failed": (
        "😕 Sorry, generation failed ({reason}). Your {credits} credit(s) were refunded.\n"
        "Reply *retry* to try again — you won't be charged twice."
    ),
    "retry_started": "🔁 Retrying your video now.",
    "nothing_to_retry": "There's no failed video to retry.",
    "revision_summary_intro": "Got it — changes for revision {revision}: {changes}",
    "no_changes_detected": (
        "What would you like to change? e.g. \"make it more cinematic\", \"use a faster pace\", "
        "\"change the voice to Malayalam\"."
    ),
    "resent_link": "Here's a fresh download link (expires in {hours} h): {url}",
    "no_video_yet": "You don't have a finished video yet.",
    "clarify": (
        "I'm not sure I got that. You can describe a video (\"a funny 15s Reel about cats\"), "
        "or reply *help*."
    ),
    "confirm_hint": "Reply *yes* to generate, or tell me what to change.",
    "ack_new_project": "Starting a fresh video. What should it be about?",
}


def render(purpose: str, facts: dict[str, Any] | None = None) -> str:
    facts = dict(facts or {})
    if "topic" in facts:
        facts["topic"] = facts["topic"] or "your video"
    if "status" in facts:
        facts["status"] = label(facts["status"]).lower()
    name = facts.get("name")
    facts["name"] = f" {name}" if name and purpose == "welcome" else (name or "")
    template = TEMPLATES.get(purpose, TEMPLATES["clarify"])
    try:
        return template.format(**facts)
    except (KeyError, IndexError):
        return template
