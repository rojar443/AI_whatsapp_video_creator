"""Built-in sequencing: arrange the best segments into the playbook's hook → build → payoff
structure and fit them to the target duration."""
from __future__ import annotations

import re

from app.pipeline.contracts import PlanItem, SequencingInput, SequencingOutput


def _pace_factor(instructions: str | None, vibe: str | None) -> float:
    t = (instructions or "").lower()
    f = 1.0
    if re.search(r"\b(faster|quick|snappy|speed up|more cuts)\b", t):
        f *= 0.75
    if re.search(r"\b(slower|slow down|calmer|longer shots)\b", t):
        f *= 1.3
    if vibe in ("motivational", "funny"):
        f *= 0.85
    elif vibe in ("calm", "emotional"):
        f *= 1.15
    return f


class Sequencer:
    def run(self, inp: SequencingInput) -> SequencingOutput:
        pb = inp.context.playbook
        pacing = pb.get("pacing", {})
        avg = float(pacing.get("avg_clip_seconds", 3)) * _pace_factor(inp.context.instructions, inp.context.vibe)
        lo, hi = float(pacing.get("min_clip_seconds", 1.2)), float(pacing.get("max_clip_seconds", 5))
        avg = max(lo, min(hi, avg))
        hook_cfg = pb.get("hook", {})
        hook_tags = set(hook_cfg.get("prefer_tags") or [])
        trans = pb.get("visual_treatment", {})
        overlap = float(trans.get("transition_seconds", 0)) if trans.get("transition") == "fade" else 0.0
        target = float(inp.context.target_seconds)
        notes: list[str] = []

        scores = {s.segment_id: s for s in inp.scores}
        order = {a.asset_id: a.order_index for a in inp.assets}
        segs = [s for s in inp.segments if s.segment_id in scores]
        if not segs:
            return SequencingOutput(items=[], total_seconds=0, notes=["no usable footage"])
        ranked = sorted(segs, key=lambda s: scores[s.segment_id].score, reverse=True)

        def avail(seg) -> float:
            return 99.0 if seg.kind == "photo" else seg.end - seg.start

        top = scores[ranked[0].segment_id].score
        hook = next((s for s in ranked if hook_tags & set(scores[s.segment_id].tags)
                     and scores[s.segment_id].score >= top - 1.0 and avail(s) >= lo), ranked[0])
        rest = [s for s in ranked if s is not hook]
        payoff = next((s for s in rest if s.asset_id != hook.asset_id and avail(s) >= lo), rest[0] if rest else None)

        chosen = [hook] + ([payoff] if payoff else [])
        per_asset: dict[str, int] = {hook.asset_id: 1}
        if payoff:
            per_asset[payoff.asset_id] = per_asset.get(payoff.asset_id, 0) + 1

        def planned_len(seg, role) -> float:
            cap = float(hook_cfg.get("max_seconds", 3)) if role == "hook" else hi
            want = avg * (1.15 if seg.kind == "photo" else 1.0)
            return round(max(min(lo, avail(seg)), min(want, cap, avail(seg))), 2)

        total = sum(planned_len(s, "hook" if s is hook else "payoff") for s in chosen)
        for s in rest:
            if s is payoff:
                continue
            if total - overlap * (len(chosen) - 1) >= target:
                break
            if avail(s) < lo or per_asset.get(s.asset_id, 0) >= 2 or scores[s.segment_id].score < 2:
                continue
            chosen.append(s)
            per_asset[s.asset_id] = per_asset.get(s.asset_id, 0) + 1
            total += planned_len(s, "build")

        build = [s for s in chosen if s is not hook and s is not payoff]
        if pb.get("structure", {}).get("order", "chronological_after_hook") == "chronological_after_hook":
            build.sort(key=lambda s: (order.get(s.asset_id, 0), s.start))
        ordered = [(hook, "hook"), *[(s, "build") for s in build], *([(payoff, "payoff")] if payoff else [])]

        lengths = [planned_len(s, r) for s, r in ordered]
        effective = sum(lengths) - overlap * (len(lengths) - 1)
        if effective < target - 0.5:
            # stretch shots that have room (photos can hold longer) before giving up
            for _ in range(3):
                gap = target - (sum(lengths) - overlap * (len(lengths) - 1))
                if gap <= 0.1:
                    break
                room = [i for i, (s, _) in enumerate(ordered) if avail(s) - lengths[i] > 0.2 and lengths[i] < hi * 1.4]
                if not room:
                    break
                add = gap / len(room)
                for i in room:
                    lengths[i] = round(min(avail(ordered[i][0]), lengths[i] + add, hi * 1.4), 2)
            effective = sum(lengths) - overlap * (len(lengths) - 1)
            if effective < target - 0.5:
                notes.append(f"You have about {effective:.0f} s of usable footage, so the reel will be "
                             f"{effective:.0f} s instead of {target:.0f} s.")
        elif effective > target + 0.3:
            excess = effective - target
            for i in sorted(range(len(lengths)), key=lambda i: -lengths[i]):  # trim longest shots first
                cut = min(excess, max(0.0, lengths[i] - lo))
                lengths[i] = round(lengths[i] - cut, 2)
                excess -= cut
                if excess <= 0.05:
                    break

        items = []
        for (seg, role), length in zip(ordered, lengths, strict=True):
            sc = scores[seg.segment_id]
            if seg.kind == "video":
                mid = (seg.start + seg.end) / 2
                start = max(seg.start, min(mid - length / 2, seg.end - length))
                s0, s1 = round(start, 2), round(start + length, 2)
            else:
                s0, s1 = 0.0, length
            items.append(PlanItem(segment_id=seg.segment_id, asset_id=seg.asset_id, kind=seg.kind, source_start=s0,
                                  source_end=s1, role=role, reason=sc.reason, description=sc.description,
                                  focus_x=sc.focus_x))
        total_out = round(sum(i.source_end - i.source_start for i in items) - overlap * max(0, len(items) - 1), 2)
        return SequencingOutput(items=items, total_seconds=total_out, notes=notes)
