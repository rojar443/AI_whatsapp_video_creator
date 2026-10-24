"""Reply templates. Short and scoped to making reels (Meta: no general-purpose assistant)."""
from typing import Any

TEMPLATES: dict[str, str] = {
    "first_contact": (
        "Hi{name}! 👋 I turn your clips and photos into reels for Instagram and YouTube Shorts.\n"
        "Send me a few videos or photos, then tell me the vibe (e.g. *cinematic travel reel*). "
        "Text or voice notes both work.\n\n"
        "Your media and messages are processed by AI services to edit your reel. Privacy: {web_url}/privacy"
    ),
    "help": (
        "How it works:\n1. Send your clips and photos\n2. Say *that's all* and describe the vibe\n"
        "3. I'll show a plan — reply *yes* to make it\n4. Ask for changes like *shorter* or *start with the beach*\n\n"
        "Commands: *status* · *new* · *cancel* · *retry* · *link* (download link) · *register*"
    ),
    "batch_ack": "Got {summary}. Send more, or tell me what you want to create.",
    "batch_ack_prefs": "Got {summary}. Send more, or say *that's all* when you're done.",
    "send_media_first": "Great idea! Now send me the clips and photos you'd like to use, then say *that's all*.",
    "need_media": "I don't have any clips yet — send me a few videos or photos first.",
    "ask_genre": (
        "Nice — {summary}. What kind of reel is this?\n"
        "Reply *travel* or *daily life*, and add a vibe if you like (cinematic, calm, funny, emotional…)."
    ),
    "planning": "Got it! Picking the best moments from {summary}… ⏳",
    "replanning": "Updating the plan… ⏳",
    "plan_failed": "Sorry, I couldn't put a plan together ({reason}). Try sending a few more clips, or reply *retry*.",
    "confirm_hint": "Reply *yes* to make the reel, or tell me what to change.",
    "plan_stale": "That plan is out of date. Here's the latest one above — reply *yes* to go ahead.",
    "nothing_to_confirm": "There's no plan waiting for your OK right now.",
    "queued": "🚀 Your reel is being processed. I'll send it here when it's ready.",
    "processing": "🎬 Editing your reel now…",
    "already_processing": "Your reel is already being made — I'll send it as soon as it's ready.",
    "busy_modify": "Your reel is being made right now. Once it arrives you can ask for changes.",
    "delivered": "✅ Your reel is ready! Want any changes? (e.g. \"shorter\", \"more cinematic\", \"start with the beach\")",
    "delivered_link": "✅ Your reel *{title}* is ready! Download (link expires in {hours} h):",
    "demo_notice": "⚠️ Demo mode: this reel used offline stand-ins ({what}) instead of the real AI services.",
    "anon_register_nudge": "Want to keep your reels and get more credits? Register at {web_url}/register?phone={phone} and link this chat.",
    "approve": "Glad you like it! 🎉 Send more clips anytime for a new reel.",
    "failed": "😕 Sorry, I couldn't make your reel ({reason}). Your {credits} credit(s) were refunded.\nReply *retry* to try again — you won't be charged twice.",
    "retry_started": "🔁 Trying again now.",
    "nothing_to_retry": "There's nothing to retry.",
    "insufficient_credits": "This reel needs {cost} credits but you have {balance}. Choose a shorter length, or register at {web_url}/register for more credits.",
    "anon_limit": "You've used your free drafts. Register at {web_url}/register?phone={phone} to keep going — it takes a minute.",
    "daily_limit": "You've reached today's limit of {limit} reels. Please try again tomorrow.",
    "cancelled": "Okay, cancelled. Send new clips whenever you're ready.",
    "cancelled_refund": "Okay, cancelled — {credits} credit(s) refunded.",
    "kept_previous": "Okay, I kept your previous version.",
    "nothing_to_cancel": "There's nothing in progress to cancel.",
    "cannot_cancel_processing": "Editing has already started and can't be stopped, sorry.",
    "status_none": "No reel in progress. Send me some clips or photos to start!",
    "status": "Your reel *{title}* is {status}.",
    "voice_failed": "Sorry, I couldn't understand that voice note. Could you try again, or type it?",
    "media_rejected": "I couldn't use {what}: {reason}.",
    "media_failed": "I couldn't download {what}. Please send it again.",
    "too_many_files": "That's the maximum of {limit} files for one reel. Say *that's all* to continue.",
    "unsupported_media": "I can use videos, photos, text and voice notes.",
    "unsupported": "Heads up: {items}.",
    "off_topic": "I can only help turn your clips and photos into reels 🙂 Send me some videos or photos to get started.",
    "no_changes_detected": "What would you like to change? e.g. \"shorter\", \"faster cuts\", \"no music\", \"start with the sunset\".",
    "revision_intro": "Got it — revision {revision}: {changes}",
    "resent_link": "Here's a fresh download link (expires in {hours} h): {url}",
    "no_video_yet": "You don't have a finished reel yet.",
    "register": "Create your account at {web_url}/register?phone={phone} — then send me the *LINK* code from your dashboard to connect this chat.",
    "not_registered_link": "This code didn't match. Generate a new one on your dashboard: {web_url}/dashboard",
    "linked": "✅ Linked to {name}'s ReelChat account. Your reels will appear on your dashboard: {web_url}/dashboard",
    "already_linked": "This number is already linked to your account ✅",
    "rate_limited": "You're sending messages very quickly — please wait a minute.",
    "suspended": "This account is suspended. Contact support if you think this is a mistake.",
    "clarify": "I'm not sure I got that. Send clips or photos, or describe the reel you want (e.g. *calm daily-life reel, 30 seconds*).",
    "ack_prefs": "Noted: {changes}.",
    "new_project": "Starting fresh. Send me the clips and photos for your next reel.",
}


def render(purpose: str, facts: dict[str, Any] | None = None) -> str:
    facts = dict(facts or {})
    if purpose == "first_contact":
        facts["name"] = f" {facts['name']}" if facts.get("name") else ""
    template = TEMPLATES.get(purpose, TEMPLATES["clarify"])
    try:
        return template.format(**facts)
    except (KeyError, IndexError):
        return template


def media_summary(videos: int, photos: int) -> str:
    parts = []
    if videos:
        parts.append(f"{videos} clip{'s' if videos != 1 else ''}")
    if photos:
        parts.append(f"{photos} photo{'s' if photos != 1 else ''}")
    return " and ".join(parts) or "no media yet"
