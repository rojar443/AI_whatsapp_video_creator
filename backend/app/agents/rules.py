"""Deterministic keyword-based agent.

Used for development, tests and as an automatic fallback when an LLM provider fails.
It is intentionally conservative: when unsure it returns intent 'other'.
"""
from __future__ import annotations

import re
from typing import Any

from app.agents.base import AgentContext, AgentProvider, MessageAnalysis, Scene, Script
from app.conversation import requirements as req
from app.conversation.safety import is_explicit_confirmation, normalize

LANG_WORDS = {
    "english": ["english", "inglish"],
    "hindi": ["hindi", "हिंदी", "हिन्दी"],
    "malayalam": ["malayalam", "മലയാളം"],
    "tamil": ["tamil", "தமிழ்"],
    "spanish": ["spanish", "español", "espanol"],
    "french": ["french", "français", "francais"],
}
UNSUPPORTED_LANGS = ["german", "japanese", "chinese", "mandarin", "arabic", "russian", "korean",
                     "portuguese", "italian", "bengali", "telugu", "kannada", "marathi"]
VIBE_WORDS = {
    "motivational": ["motivational", "motivating", "motivation", "hype", "pump up"],
    "funny": ["funny", "humor", "humour", "comedy", "hilarious", "comic", "lol"],
    "emotional": ["emotional", "touching", "heartfelt", "sad", "sentimental"],
    "inspirational": ["inspirational", "inspiring", "uplifting"],
    "educational": ["educational", "informative", "explainer", "tutorial", "tips", "how to", "how-to"],
    "calm": ["calm", "relaxing", "peaceful", "chill", "soothing"],
    "energetic": ["energetic", "high energy", "high-energy", "exciting"],
}
STYLE_WORDS = {
    "cinematic": ["cinematic", "filmic", "movie-like", "epic look"],
    "realistic": ["realistic", "real footage", "live action", "live-action", "photorealistic"],
    "animation": ["animation", "animated", "cartoon", "2d", "3d"],
    "documentary": ["documentary", "docu"],
    "minimalist": ["minimalist", "minimal", "clean look", "simple look"],
}
MUSIC_WORDS = {
    "upbeat": ["upbeat", "energetic", "happy", "fun", "pumping"],
    "calm": ["calm", "soft", "relaxing", "chill", "lofi", "lo-fi", "ambient"],
    "dramatic": ["dramatic", "epic", "intense"],
    "inspiring": ["inspiring", "inspirational", "uplifting"],
}
NUMBER_WORDS = {"fifteen": 15, "thirty": 30, "forty five": 45, "forty-five": 45, "sixty": 60}

FILLER = {
    "a", "an", "the", "me", "my", "some", "short", "quick", "video", "videos", "reel", "reels",
    "clip", "please", "pls", "create", "make", "generate", "produce", "want", "need", "i", "can",
    "you", "could", "would", "like", "to", "for", "insta", "instagram", "youtube", "shorts",
    "tiktok", "vertical", "second", "seconds", "sec", "s", "and", "with", "of", "new", "one",
}

GREETING_RE = re.compile(r"^(hi|hello|hey|hii+|namaste|namaskaram|good (morning|afternoon|evening)|yo)\b")


def _has(text: str, words: list[str]) -> bool:
    return any(re.search(rf"(?<![\w]){re.escape(w)}(?![\w])", text) for w in words)


def _first_match(text: str, table: dict[str, list[str]]) -> str | None:
    best: tuple[int, str] | None = None
    for key, words in table.items():
        for w in words:
            m = re.search(rf"(?<![\w]){re.escape(w)}(?![\w])", text)
            if m and (best is None or m.start() < best[0]):
                best = (m.start(), key)
    return best[1] if best else None


class RulesAgentProvider(AgentProvider):
    name = "rules"
    is_llm = False

    # ------------------------------------------------------------------ extraction
    def _extract(self, raw: str, ctx: AgentContext) -> tuple[dict[str, Any], list[str]]:
        t = normalize(raw)
        fields: dict[str, Any] = {}
        unsupported: list[str] = []
        work = t

        # Music first, so "inspiring music" doesn't also set the vibe.
        m = re.search(r"\b(no|without)\s+(background\s+)?music\b", work)
        if m:
            fields["music"] = "none"
            work = work.replace(m.group(0), " ")
        else:
            m = re.search(r"([\w-]+)\s+(?:background\s+)?(music|soundtrack|track|beat)s?\b", work)
            if m and (mood := _first_match(m.group(1), MUSIC_WORDS)):
                fields["music"] = mood
                work = work.replace(m.group(0), " ")
            elif re.search(r"\bwith music\b", work):
                work = work.replace("with music", " ")
        if re.search(r"\b(song|songs)\b", work) and re.search(r"\bby\b|\bsong\b", work):
            unsupported.append("specific commercial songs (I use royalty-free background music)")

        # Voice
        if re.search(r"\b(no|without)\s+(voice|voiceover|voice-over|narration|narrator)\b|\bmusic only\b", work):
            fields["voice"] = "none"
        elif re.search(r"\b(male|man'?s?|guy|deep)\s+voice\b|\bmale narrator\b", work):
            fields["voice"] = "male"
        elif re.search(r"\b(female|woman'?s?|lady|girl'?s?)\s+voice\b|\bfemale narrator\b", work):
            fields["voice"] = "female"

        # Captions
        if re.search(r"\b(no|without|turn off|remove|disable)\s+(the\s+)?(captions?|subtitles?|subs)\b", work):
            fields["captions_enabled"] = False
        elif re.search(r"\b(captions?|subtitles?|subs)\b", work):
            fields["captions_enabled"] = True
            m = re.search(r"\b(?:captions?|subtitles?|subs)\s+in\s+(\w+)", work)
            if m and (lang := _first_match(m.group(1), LANG_WORDS)):
                fields["caption_language"] = lang
                work = work.replace(m.group(0), " ")

        # Language
        lang = _first_match(work, LANG_WORDS)
        if lang:
            fields["language"] = lang
        else:
            for ul in UNSUPPORTED_LANGS:
                if _has(work, [ul]):
                    unsupported.append(
                        f"{ul.capitalize()} narration (available: English, Hindi, Malayalam, Tamil, Spanish, French)"
                    )
                    break

        # Duration
        duration = None
        m = re.search(r"\b(\d{1,3})\s*-?\s*(s|sec|secs|second|seconds)\b", work)
        if m:
            duration = int(m.group(1))
        elif re.search(r"\b(1|one|a)\s*-?\s*(min|minute)\b", work):
            duration = 60
        elif re.search(r"\b(2|two|3|three|5|five)\s*-?\s*(min|minutes|mins)\b", work):
            duration = 120
        elif "half a minute" in work:
            duration = 30
        else:
            for word, n in NUMBER_WORDS.items():
                if re.search(rf"\b{word}\s*(s|sec|secs|seconds?)\b", work):
                    duration = n
        if duration is not None:
            if duration in req.DURATIONS:
                fields["duration_seconds"] = duration
            else:
                nearest = req.nearest_duration(duration)
                unsupported.append(f"{duration}-second length (I can do 15, 30, 45 or 60 s) — using {nearest} s")
                fields["duration_seconds"] = nearest
        elif re.search(r"\b(longer|make it longer)\b", work) and ctx.prefs.get("duration_seconds"):
            cur = ctx.prefs["duration_seconds"]
            bigger = [d for d in req.DURATIONS if d > cur]
            if bigger:
                fields["duration_seconds"] = bigger[0]
        elif re.search(r"\b(shorter|make it shorter)\b", work) and ctx.prefs.get("duration_seconds"):
            cur = ctx.prefs["duration_seconds"]
            smaller = [d for d in req.DURATIONS if d < cur]
            if smaller:
                fields["duration_seconds"] = smaller[-1]

        # Format & aspect ratio
        if re.search(r"\b(youtube|yt)\b|\bshorts\b", work):
            fields["format"] = "youtube_short"
        elif re.search(r"\b(instagram|insta|ig)\b|\breels?\b", work):
            fields["format"] = "instagram_reel"
        elif re.search(r"\b(tiktok|vertical|generic)\b", work):
            fields["format"] = "vertical"
        if re.search(r"\b(16:9|landscape|horizontal|widescreen|1:1|square|4:5)\b", work):
            unsupported.append("non-vertical formats (videos are 9:16 vertical)")

        # Quality
        if re.search(r"\b(4k|8k|2160p)\b", work):
            unsupported.append("4K (best available is high quality 1080×1920)")
            fields["quality"] = "high"
        elif re.search(r"\b(high|best|hd|full hd|1080p?|premium|top)\s*-?\s*(quality|res|resolution)?\b", work) and \
                re.search(r"quality|hd|1080|res", work):
            fields["quality"] = "high"
        elif re.search(r"\b(standard|normal|basic|regular)\s+quality\b|\bsd\b", work):
            fields["quality"] = "standard"

        # Pace
        if re.search(r"\b(faster|fast|quick|quicker|snappy|speed it up)\b(\s+pace)?", work) and \
                not re.search(r"\bfast food\b", work):
            fields["pace"] = "fast"
        elif re.search(r"\b(slower|slow|relaxed pace)\b", work):
            fields["pace"] = "slow"

        # Vibe & style
        vibe = _first_match(work, VIBE_WORDS)
        if vibe:
            fields["vibe"] = vibe
        style = _first_match(work, STYLE_WORDS)
        if style:
            fields["visual_style"] = style

        # Branding / CTA / script
        m = re.search(r"\b(?:call to action|cta)\s*(?:is|:|-|=)?\s*[\"“']?(.+?)[\"”']?$", raw.strip(), re.I)
        if m:
            fields["call_to_action"] = m.group(1)
        m = re.search(r"\bend with\s+[\"“'](.+?)[\"”']", raw, re.I)
        if m:
            fields["call_to_action"] = m.group(1)
        m = re.search(r"\bbrand colou?rs?\s*(?:are|is|:|-)?\s*(.+)$", raw.strip(), re.I)
        if m:
            fields["brand_colors"] = m.group(1)
        if re.search(r"\blogo\b", work):
            unsupported.append("logo overlays (coming soon — brand colours and a call to action work today)")
        m = re.search(r"\b(?:my script|use this script|script)\s*[:\-]\s*(.+)$", raw.strip(), re.I | re.S)
        if m and len(m.group(1)) > 20:
            fields["user_script"] = m.group(1)

        # Topic
        topic = self._topic(raw, t, ctx)
        if topic:
            fields["topic"] = topic
        return fields, unsupported

    def _topic(self, raw: str, t: str, ctx: AgentContext) -> str | None:
        m = re.search(r"\b(?:change|switch|set)\s+the\s+topic\s+(?:to|:)\s*(.+)$", t)
        if m:
            return self._clean_topic(m.group(1))
        m = re.search(r"\b(?:about|on the topic of|regarding|featuring|showing|explaining)\s+(.+)$", t)
        if m:
            return self._clean_topic(m.group(1))
        m = re.search(
            r"\b(?:create|make|generate|produce|want|need)\s+(?:me\s+)?(?:a|an|some)?\s*(.+?)\s+(?:video|reel|short|clip)s?\b",
            t,
        )
        if m:
            cand = self._clean_topic(m.group(1))
            if cand:
                return cand
        if ctx.pending_group == "topic" or (ctx.state in ("none", "draft") and not ctx.prefs.get("topic")):
            if ctx.pending_group == "topic":
                cand = self._clean_topic(t)
                if cand and len(cand) >= 2:
                    return cand
        return None

    @staticmethod
    def _clean_topic(text: str) -> str | None:
        text = re.sub(r"\b\d{1,3}\s*-?\s*(?:s|sec|secs|seconds?)\b", " ", text)
        text = re.split(
            r"[,.;!?]|\s+(?:in|with|using|and make|make it|for (?:instagram|insta|youtube|tiktok|reels?|shorts))\b",
            text,
        )[0]
        words = re.findall(r"[\w'&+-]+", text)
        drop = set(FILLER)
        for table in (VIBE_WORDS, STYLE_WORDS, LANG_WORDS):
            for ws in table.values():
                drop.update(ws)
        # Trim filler only at the edges: "a funny cat video" → "cat"; "tips for runners" stays.
        while words and words[0].lower() in drop:
            words.pop(0)
        while words and (words[-1].lower() in drop or words[-1].lower() in {"about", "on"}):
            words.pop()
        topic = " ".join(words).strip()
        return topic[:300] or None

    # ------------------------------------------------------------------ intent
    def analyze_message(self, text: str, ctx: AgentContext) -> MessageAnalysis:
        t = normalize(text)
        fields, unsupported = self._extract(text, ctx)
        intent = "other"
        conf = 0.6
        if not t:
            intent = "other"
        elif is_explicit_confirmation(t):
            intent, conf = "confirm", 0.95
        elif re.fullmatch(r"(help|menu|\?|what can you do|how does (this|it) work)", t):
            intent, conf = "help", 0.95
        elif re.match(r"^(cancel|stop|abort|never ?mind|forget it)\b", t):
            intent, conf = "cancel", 0.9
        elif re.match(r"^(retry|try again|regenerate)\b", t):
            intent, conf = "retry", 0.9
        elif re.search(r"\b(status|progress|is it (ready|done)|where is my video|how long)\b", t):
            intent, conf = "status", 0.85
        elif re.search(r"\b(link|resend|send (it|the video) again|download)\b", t) and len(t) < 60:
            intent, conf = "resend_link", 0.8
        elif re.search(r"\b(new (video|project|one|reel)|another (video|one|reel)|start over|start again)\b", t) \
                and not fields.get("topic"):
            intent, conf = "new_project", 0.85
        elif re.search(r"\b(create|make|generate|produce|want|need)\b.*\b(video|reel|short|clip)s?\b", t):
            intent, conf = "create_video", 0.85
        elif GREETING_RE.match(t) and len(t) < 30 and not fields:
            intent, conf = "greeting", 0.9
        elif fields and (
            ctx.state in ("completed", "failed", "awaiting_confirmation")
            or re.search(r"\b(make it|change|switch|instead|rather|more|less|use)\b", t)
        ):
            intent, conf = "modify", 0.75
        elif fields or (ctx.pending_group and len(t) > 1):
            intent, conf = "provide_info", 0.7
        elif re.fullmatch(r"(you (choose|decide|pick)|anything|whatever|surprise me|default|defaults|any)", t):
            intent, conf = "provide_info", 0.8
        is_revision = intent == "modify"
        return MessageAnalysis(
            intent=intent, confidence=conf, fields=fields, unsupported=unsupported, is_revision=is_revision
        )

    # ------------------------------------------------------------------ script
    def write_script(self, prefs: dict[str, Any], scene_count: int, words_per_scene: int) -> Script:
        topic = prefs.get("topic") or "your idea"
        vibe = prefs.get("vibe") or "inspirational"
        cta = prefs.get("call_to_action") or "Follow for more."
        hooks = {
            "motivational": f"Ready to level up your {topic}?",
            "funny": f"Nobody talks about the funny side of {topic}.",
            "emotional": f"Some moments in {topic} stay with you forever.",
            "inspirational": f"Every journey in {topic} starts with one step.",
            "educational": f"Three things to know about {topic}.",
            "calm": f"Take a breath. Let's talk about {topic}.",
            "energetic": f"Let's go — {topic}, full power!",
        }
        body = [
            f"Start small with {topic} — consistency beats intensity.",
            "Show up every day, even when it's hard.",
            "Track your progress and celebrate small wins.",
            "Learn from the best and make it your own.",
            "Remember why you started.",
            "Your future self will thank you.",
            "Keep it simple and keep going.",
            "Share it with someone who needs this.",
        ]
        lines = [hooks.get(vibe, hooks["inspirational"])]
        lines += body[: max(0, scene_count - 2)]
        lines.append(cta)
        scenes = [
            Scene(
                narration=line,
                on_screen_text=line if len(line) <= 60 else line[:57] + "…",
                visual_query=f"{topic} {vibe}",
            )
            for line in lines[:scene_count]
        ]
        tag = re.sub(r"[^a-z0-9]", "", topic.lower())[:30] or "video"
        return Script(title=topic.capitalize()[:80], scenes=scenes, hashtags=[f"#{tag}", f"#{vibe}"])
