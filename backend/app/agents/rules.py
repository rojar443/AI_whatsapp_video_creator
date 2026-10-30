"""Deterministic keyword agent: offline default for development/tests and the automatic
fallback when an LLM provider fails. Conservative — returns 'other' when unsure."""
from __future__ import annotations

import re
from typing import Any

from app.agents.base import AgentContext, AgentProvider, MessageAnalysis
from app.conversation.safety import is_done_phrase, is_explicit_confirmation, normalize
from app.pipeline.contracts import EditContext, PlanItem, ScriptLine, ScriptOutput

GENRE_WORDS = {
    "travel": ["travel", "trip", "vacation", "holiday", "journey", "tour", "road trip", "beach", "mountains",
               "hiking", "sightseeing", "backpacking", "getaway"],
    "daily_life": ["daily life", "day in my life", "day in the life", "everyday", "daily", "routine", "vlog",
                   "my day", "family", "weekend", "life update", "morning routine"],
}
VIBE_WORDS = {
    "cinematic": ["cinematic", "epic", "filmic", "movie"],
    "motivational": ["motivational", "motivating", "inspiring", "hype"],
    "funny": ["funny", "fun", "humor", "humour", "comedy", "hilarious"],
    "emotional": ["emotional", "touching", "heartfelt", "nostalgic", "sentimental"],
    "professional": ["professional", "corporate", "business", "clean"],
    "educational": ["educational", "informative", "tips", "how to", "tutorial", "guide"],
    "calm": ["calm", "relaxing", "chill", "peaceful", "cozy", "cosy", "soft"],
}
STYLE_WORDS = {
    "cinematic": ["cinematic look", "cinematic style", "film look", "cinematic grade", "cinematic"],
    "natural": ["natural", "realistic", "true to life"],
    "vibrant": ["vibrant", "colorful", "colourful", "bright", "punchy", "saturated"],
    "minimalist": ["minimal", "minimalist", "simple", "clean look"],
    "documentary": ["documentary", "docu", "raw"],
}
OTHER_LANGS = ["malayalam", "hindi", "tamil", "spanish", "french", "german", "arabic", "japanese", "portuguese"]
INSTRUCTION_RE = re.compile(
    r"\b(start|open|begin|end|finish|close)\s+(with|on)\b|\b(use|include|remove|drop|skip|cut|trim|crop|focus on|"
    r"emphasi[sz]e|highlight|more of|less of|faster|slower|speed up|slow down|shorter|longer|zoom|reorder|"
    r"put|move|swap)\b",
    re.I,
)
OFF_TOPIC_RE = re.compile(
    r"\b(weather|news|joke|recipe|homework|code|translate|who is|what is the capital|stock price|"
    r"bitcoin|football score|write (me )?(an? )?(essay|poem|email))\b", re.I)
GREETING_RE = re.compile(r"^(hi|hello|hey|hii+|namaste|good (morning|afternoon|evening)|yo)\b")


def _first(text: str, table: dict[str, list[str]]) -> str | None:
    best: tuple[int, str] | None = None
    for key, words in table.items():
        for w in words:
            m = re.search(rf"(?<![\w]){re.escape(w)}(?![\w])", text)
            if m and (best is None or m.start() < best[0]):
                best = (m.start(), key)
    return best[1] if best else None


class RulesAgentProvider(AgentProvider):
    name = "rules"

    def _extract(self, raw: str, ctx: AgentContext) -> tuple[dict[str, Any], list[str]]:
        t = normalize(raw)
        f: dict[str, Any] = {}
        unsupported: list[str] = []

        if g := _first(t, GENRE_WORDS):
            f["genre"] = g
        # music before vibe so "soft music" doesn't become a vibe only
        if re.search(r"\b(no|without)\s+(music|audio|sound)\b|\b(mute|silent)\b", t):
            f["music"] = "none"
        elif re.search(r"\b(keep|retain|original|real)\s+(the\s+)?(original\s+)?(audio|sound|voices?|speech)\b", t):
            f["music"] = "original"
        elif re.search(r"\b(music|song|soundtrack|beat|track)\b", t):
            f["music"] = "licensed"
            if re.search(r"\b(song|track)\s+by\b|\bby\s+[a-z]+ [a-z]+\b.*\bsong\b", t):
                unsupported.append("specific commercial songs (I use licensed library music)")
        # narration
        if re.search(r"\b(no|without)\s+(voice ?over|narration|narrator|voice)\b", t):
            f["narration"] = "none"
        elif re.search(r"\b(my (own )?voice|in my voice|clone)\b", t):
            f["narration"] = "default"
            unsupported.append("narration in your own cloned voice (coming soon — I'll use an AI voice)")
        elif re.search(r"\b(voice ?over|narration|narrate|narrator|storytelling)\b", t):
            f["narration"] = "default"
        # captions
        if re.search(r"\b(no|without|remove|turn off)\s+(the\s+)?(captions?|subtitles?|text)\b", t):
            f["captions"] = False
        elif re.search(r"\b(captions?|subtitles?)\b", t):
            f["captions"] = True
        # duration
        m = re.search(r"\b(\d{1,3})\s*-?\s*(s|sec|secs|second|seconds)\b", t)
        if m:
            n = int(m.group(1))
            nearest = min((15, 30, 60), key=lambda d: abs(d - n))
            f["duration"] = str(nearest)
            if n not in (15, 30, 60):
                unsupported.append(f"{n}-second length (I can do 15, 30 or 60 s, or the original length) — using {nearest} s")
        elif "half a minute" in t:
            f["duration"] = "30"
        elif re.search(r"\b(1|one|a)\s*-?\s*min(ute)?\b", t):
            f["duration"] = "60"
        elif re.search(r"\b(original|full|whole)\s+(length|duration)\b|\bkeep (it|the) (length|duration)\b", t):
            f["duration"] = "original"
        elif re.search(r"\bshorter\b", t) and ctx.prefs.get("duration") in ("60", "30"):
            f["duration"] = {"60": "30", "30": "15"}[ctx.prefs["duration"]]
        elif re.search(r"\blonger\b", t) and ctx.prefs.get("duration") in ("15", "30"):
            f["duration"] = {"15": "30", "30": "60"}[ctx.prefs["duration"]]
        # format
        if re.search(r"\b(youtube|yt)\b|\bshorts\b", t):
            f["format"] = "youtube_short"
        elif re.search(r"\b(instagram|insta|ig)\b|\breels?\b", t):
            f["format"] = "instagram_reel"
        elif re.search(r"\b(tiktok|vertical)\b", t):
            f["format"] = "vertical"
        if re.search(r"\b(16:9|landscape|horizontal|widescreen|square|1:1)\b", t):
            unsupported.append("non-vertical formats (reels are 9:16)")
        # quality
        if re.search(r"\b4k\b", t):
            unsupported.append("4K (best is high quality 1080×1920)")
            f["quality"] = "high"
        elif re.search(r"\b(high|best|hd|1080p?)\s*-?\s*(quality|res|resolution)\b|\bfull hd\b|\bhd\b", t):
            f["quality"] = "high"
        elif re.search(r"\b(standard|normal)\s+quality\b", t):
            f["quality"] = "standard"
        # vibe & style ("cinematic" sets both)
        if v := _first(re.sub(r"\b\w+\s+(music|song|track|beat|voice ?over|voice)\b", " ", t), VIBE_WORDS):
            f["vibe"] = v
        if s := _first(t, STYLE_WORDS):
            f["style"] = s
        # language
        for lang in OTHER_LANGS:
            if re.search(rf"\b{lang}\b", t):
                unsupported.append(f"{lang.capitalize()} narration (English only for now)")
                break
        # title
        m = re.search(r"\btitle\s*(?:is|:|-)?\s*[\"“'](.+?)[\"”']", raw, re.I)
        if m:
            f["title_text"] = m.group(1)
        # free-form edit instructions (passed to selection/sequencing)
        if INSTRUCTION_RE.search(t):
            f["instructions"] = raw.strip()[:500]
        return f, unsupported

    def analyze_message(self, text: str, ctx: AgentContext) -> MessageAnalysis:
        t = normalize(text)
        fields, unsupported = self._extract(text, ctx)
        intent, conf = "other", 0.6
        if not t:
            pass
        elif is_explicit_confirmation(t):
            intent, conf = "confirm", 0.95
        elif re.fullmatch(r"(help|menu|\?|what can you do|how does (this|it) work)", t):
            intent, conf = "help", 0.95
        elif re.match(r"^(cancel|stop|abort|never ?mind|forget it)\b", t):
            intent, conf = "cancel", 0.9
        elif re.match(r"^(retry|try again)\b", t):
            intent, conf = "retry", 0.9
        elif re.search(r"\b(status|progress|is it (ready|done)|where is my (reel|video))\b", t):
            intent, conf = "status", 0.85
        elif re.search(r"\b(link|resend|download)\b", t) and len(t) < 50 and not fields:
            intent, conf = "resend_link", 0.8
        elif re.search(r"\b(register|sign ?up|create (an )?account|website|dashboard)\b", t) and not fields:
            intent, conf = "register", 0.8
        elif re.search(r"\b(new (reel|video|project|one)|another (reel|video|one)|start over|start again)\b", t):
            intent, conf = "new_project", 0.85
        elif is_done_phrase(t):
            intent, conf = "done_uploading", 0.9
        elif re.search(r"\b(love it|looks? (great|good|perfect|amazing)|perfect|awesome|thanks?|thank you|great job)\b", t) \
                and ctx.state == "completed" and not fields:
            intent, conf = "approve", 0.85
        elif OFF_TOPIC_RE.search(t) and not fields:
            intent, conf = "off_topic", 0.8
        elif GREETING_RE.match(t) and len(t) < 30 and not fields:
            intent, conf = "greeting", 0.9
        elif fields and ctx.state in ("completed", "failed", "awaiting_confirmation"):
            intent, conf = "modify", 0.8
        elif re.search(r"\b(make|create|edit|turn|build)\b.*\b(reel|video|short|clip|montage)\b", t) or (
                fields.get("genre") and ctx.state in ("none", "collecting_media")):
            intent, conf = "create_reel", 0.85
        elif fields or (ctx.pending and len(t) > 1):
            intent, conf = "provide_info", 0.7
        elif re.fullmatch(r"(you (choose|decide|pick)|anything|whatever|surprise me|default|any)", t):
            intent, conf = "provide_info", 0.8
        return MessageAnalysis(intent=intent, confidence=conf, fields=fields, unsupported=unsupported,
                               done=is_done_phrase(t))  # "that's all, make it cinematic"

    def write_script(self, items: list[PlanItem], total_seconds: float, context: EditContext) -> ScriptOutput:
        travel = context.genre == "travel"
        hook = "Some places stay with you forever." if travel else "Here's a little piece of my day."
        payoff = "Until the next adventure." if travel else "Small moments, big smiles."
        build = (["Every turn brought something new.", "The light here is unreal.", "Slow mornings, endless views.",
                  "Moments like these.", "Taking it all in."] if travel else
                 ["Starting slow.", "A little bit of everything.", "The best part of the day.", "Keeping it simple.",
                  "Just living it."])
        lines = []
        bi = 0
        for i, it in enumerate(items):
            if it.role == "hook":
                text = hook
            elif it.role == "payoff":
                text = payoff
            else:
                if bi % 2:  # don't narrate every cut
                    bi += 1
                    continue
                text = build[(bi // 2) % len(build)]
                bi += 1
            lines.append(ScriptLine(item_index=i, text=text))
        title = context.title_text or ("Travel diaries" if travel else "A day in my life")
        return ScriptOutput(title=title, lines=lines)
