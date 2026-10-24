"""Human-readable plan shown before confirmation (what was picked, why, cost)."""
from __future__ import annotations

from app.conversation.preferences import Prefs, label

ROLE = {"hook": "Opening", "build": "", "payoff": "Ending"}
FLAG_TEXT = {"dark": "too dark", "shaky": "too shaky", "duplicate": "near-duplicate", "blurry": "blurry",
             "too_short": "too short"}


def mmss(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02d}"


def format_plan(plan: dict, prefs: Prefs, credits_cost: int, balance: int) -> str:
    v = prefs.values
    genre = "travel" if v.get("genre") == "travel" else "daily-life"
    items = plan.get("items", [])
    labels = plan.get("labels", {})
    narr = "AI voiceover" + (f" ({v.get('vibe')} tone)" if v.get("vibe") else "") \
        if v.get("narration") != "none" else "no narration"
    caps = "captions from the script" if v.get("captions") and v.get("narration") != "none" else (
        "captions" if v.get("captions") else "no captions")
    music = {"licensed": f"music: {plan.get('music_title') or 'licensed track'}", "original": "original sound",
             "none": "no music"}.get(v.get("music") or "licensed", "")
    lines = [
        f"Plan for your {genre} reel 🎬",
        f"• ~{round(plan.get('total_seconds', 0))} s vertical 9:16 {label(v.get('format'))}, "
        f"{label(v.get('style')).lower()} look",
        f"• {narr[:1].upper() + narr[1:]} · {caps} · {music}",
        f"Moments ({len(items)} picked from {plan.get('candidates', len(items))} usable shots):",
    ]
    shown = list(enumerate(items, 1))
    if len(shown) > 6:  # keep WhatsApp messages short: first moments + the ending
        shown = shown[:5] + [shown[-1]]
    for i, it in shown:
        name = labels.get(it["asset_id"], "clip")
        span = "" if it["kind"] == "photo" else f" {mmss(it['source_start'])}–{mmss(it['source_end'])}"
        why = it.get("description") or it.get("reason") or ""
        role = ROLE.get(it["role"], "")
        if i == len(items) and len(items) > 6:
            lines.append(f"   …+{len(items) - 6} more moments")
        lines.append(f"{i}. {role + ' — ' if role else ''}{name}{span}{': ' + why if why else ''}")
    dropped = plan.get("dropped", [])
    if dropped:
        lines.append("Skipped: " + " · ".join(f"{d['label']} ({d['reason']})" for d in dropped[:6]))
    script = plan.get("script") or {}
    first = [ln["text"] for ln in script.get("lines", [])][:2]
    if first and v.get("narration") != "none":
        lines.append(f"Script: \"{' '.join(first)}{' …' if len(script.get('lines', [])) > 2 else ''}\"")
    for note in plan.get("notes", []):
        lines.append(f"ℹ️ {note}")
    lines.append("")
    lines.append(f"Cost: {credits_cost} credit{'s' if credits_cost != 1 else ''} (you have {balance}).")
    lines.append("Shall I proceed? Reply *yes*, or tell me what to change.")
    return "\n".join(lines)
