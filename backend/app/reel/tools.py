"""Read-only tools over one analysis session.

These are what an agent (DirectorSelector) or an external orchestrator (e.g. AgenticOrg via an
MCP server) calls. None of them renders, writes user data or sends messages: validate_plan only
checks a proposal and returns it. Rendering stays in the pipeline, after the user confirms.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.reel.llm import ToolResult
from app.reel.models import Asset, Brief, PlanItem, ReelPlan, Shot, ShotScore, to_dict
from app.reel.playbooks import playbook_of, rules_for

MIN_SHOT_SECONDS = 0.8


@dataclass
class Session:
    brief: Brief
    assets: list[Asset]
    shots: list[Shot]
    scores: dict[str, ShotScore]
    workdir: str
    notes: list[str] = field(default_factory=list)

    def shot(self, sid: str) -> Shot | None:
        return next((s for s in self.shots if s.id == sid), None)

    def candidates(self) -> list[Shot]:
        return [s for s in self.shots if not self.scores[s.id].dropped]


def list_shots(session: Session, include_dropped: bool = False) -> list[dict]:
    out = []
    for s in session.shots:
        sc = session.scores[s.id]
        if sc.dropped and not include_dropped:
            continue
        out.append({"id": s.id, "kind": s.kind, "seconds": round(s.length, 2), "metrics": sc.metrics,
                    "dropped": sc.dropped, "description": sc.description, "hook": sc.hook,
                    "story": sc.story, "role": sc.role, "caption": sc.caption})
    return out


def view_shots(session: Session, ids: list[str], limit: int = 12) -> list[tuple[str, str]]:
    shots = [session.shot(i) for i in ids[:limit]]
    return [(f"Shot {s.id} ({s.kind}, {s.length:.1f}s)", s.strip) for s in shots if s and s.strip]


def validate_plan(session: Session, proposal: dict, selector: str) -> tuple[ReelPlan | None, list[str]]:
    """Check a proposed plan {title, items:[{shot_id, seconds?, caption?}]}. Returns (plan, errors)."""
    errors: list[str] = []
    target = session.brief.duration
    raw = proposal.get("items") or []
    if not raw:
        return None, ["plan has no items"]
    seen: set[str] = set()
    items: list[PlanItem] = []
    max_clip = rules_for(session.brief).max_clip_seconds
    for it in raw:
        sid = str(it.get("shot_id", ""))
        s = session.shot(sid)
        if s is None:
            errors.append(f"{sid}: unknown shot id")
            continue
        if session.scores[sid].dropped:
            errors.append(f"{sid}: was dropped ({session.scores[sid].dropped})")
            continue
        if sid in seen:
            errors.append(f"{sid}: used twice")
            continue
        seen.add(sid)
        want = float(it.get("seconds") or session.brief.shot_seconds)
        secs = round(max(MIN_SHOT_SECONDS, min(want, s.length, max_clip)), 2)
        items.append(PlanItem(sid, secs, str(it.get("caption") or "")[:60]))
    if errors:
        return None, errors
    total = sum(i.seconds for i in items)
    if total < 0.6 * target:
        available = sum(s.length for s in session.candidates())
        if available >= 0.6 * target:
            return None, [f"plan is {total:.1f}s but target is {target:.0f}s; add shots or lengthen them"]
    if total > 1.25 * target:
        return None, [f"plan is {total:.1f}s but target is {target:.0f}s; remove shots or shorten them"]
    title = str(proposal.get("title") or "")[:60]
    return ReelPlan(title, items, session.brief.genre, selector), []


# --- function-calling specs (also the shape an MCP server would advertise) ---------------

TOOL_SPECS: list[dict] = [
    {"name": "list_shots",
     "description": "List candidate shots with technical metrics and any vision scores.",
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "view_shots",
     "description": "See shots: returns one image per shot showing its start, middle and end frames.",
     "parameters": {"type": "object", "additionalProperties": False, "required": ["ids"],
                    "properties": {"ids": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "get_playbook",
     "description": "The genre playbook: hook, structure, pacing and what to prefer or avoid.",
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "submit_plan",
     "description": "Submit the reel plan in final order. Returns ok, or errors to fix and resubmit.",
     "parameters": {"type": "object", "additionalProperties": False, "required": ["title", "items"],
                    "properties": {
                        "title": {"type": "string"},
                        "items": {"type": "array", "items": {
                            "type": "object", "additionalProperties": False,
                            "required": ["shot_id", "seconds", "caption", "why"],
                            "properties": {"shot_id": {"type": "string"}, "seconds": {"type": "number"},
                                           "caption": {"type": "string"}, "why": {"type": "string"}}}}}}},
]


class ToolBox:
    """Dispatches tool calls for one session and remembers the last valid plan."""

    def __init__(self, session: Session, selector: str):
        self.session, self.selector = session, selector
        self.plan: ReelPlan | None = None
        self.reasons: dict[str, str] = {}

    def __call__(self, name: str, args: dict) -> ToolResult:
        s = self.session
        if name == "list_shots":
            return ToolResult(json.dumps(list_shots(s)))
        if name == "view_shots":
            imgs = view_shots(s, [str(i) for i in args.get("ids", [])])
            return ToolResult(json.dumps({"returned": [label for label, _ in imgs]}), imgs)
        if name == "get_playbook":
            return ToolResult(json.dumps(playbook_of(s.brief)))
        if name == "submit_plan":
            plan, errors = validate_plan(s, args, self.selector)
            if errors:
                return ToolResult(json.dumps({"ok": False, "errors": errors}))
            self.plan = plan
            self.reasons = {str(i.get("shot_id")): str(i.get("why", ""))[:200] for i in args["items"]}
            return ToolResult(json.dumps({"ok": True, "seconds": round(plan.seconds, 1)}))
        return ToolResult(json.dumps({"error": f"unknown tool {name}"}))


def session_dict(session: Session) -> dict:
    return {"brief": to_dict(session.brief), "assets": to_dict(session.assets),
            "shots": [{**to_dict(s), "length": s.length} for s in session.shots],
            "scores": to_dict(session.scores), "notes": session.notes}
