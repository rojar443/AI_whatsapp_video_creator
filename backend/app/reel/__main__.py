"""Command line: python -m app.reel [CLIPS_DIR] [--prompt ...] [--duration 30] [--provider gemini] [--no-ai]"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

from app.reel import playbooks
from app.reel.models import brief_from_prompt
from app.reel.pipeline import build_pipeline

# /work/Hackathon/files/{input,output,playbooks} when run from backend/
DEFAULT_SOURCE = "../../files/input"
DEFAULT_OUTPUT_DIR = "../../files/output"
DEFAULT_PLAYBOOK_DIR = "../../files/playbooks"
WORK_DIR = "var/reel-work"  # frames and intermediate renders; kept out of the output folder


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.reel", description="Clips + photos -> 9:16 reel")
    ap.add_argument("source", nargs="?", default=os.environ.get("REEL_SOURCE_DIR", DEFAULT_SOURCE),
                    help=f"folder of videos and photos (default: $REEL_SOURCE_DIR or {DEFAULT_SOURCE})")
    ap.add_argument("--prompt", default="cinematic travel reel",
                    help="the user's request; pace (fast/slow), style and genre are read from it")
    ap.add_argument("--genre", help="override the genre read from --prompt (any playbook name)")
    ap.add_argument("--playbook", help="playbook JSON file for this run (overrides the genre's playbook)")
    ap.add_argument("--playbook-dir", default=os.environ.get("REEL_PLAYBOOK_DIR", DEFAULT_PLAYBOOK_DIR),
                    help=f"folder of <genre>.json playbooks, searched before the bundled ones "
                         f"(default: $REEL_PLAYBOOK_DIR or {DEFAULT_PLAYBOOK_DIR})")
    ap.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE",
                    help="override one playbook value, e.g. --set pacing.avg_clip_seconds=1.2 (repeatable)")
    ap.add_argument("--title", default="", help="hook text on the opening shot, e.g. the place name")
    ap.add_argument("--duration", type=float, help="seconds (default: middle of the playbook's length range)")
    ap.add_argument("--music", help="optional music file")
    ap.add_argument("--out", help="output MP4 (default: $REEL_OUTPUT_DIR or "
                                  f"{DEFAULT_OUTPUT_DIR}, as reel_<timestamp>.mp4)")
    ap.add_argument("--provider", choices=["gemini", "openai"],
                    default=os.environ.get("REEL_LLM_PROVIDER", "gemini"), help="vision model vendor")
    ap.add_argument("--model", help="vision model (default: $REEL_LLM_MODEL, else the vendor default)")
    ap.add_argument("--selector", choices=["timeline", "director", "heuristic"], default="timeline",
                    help="timeline: model returns timestamped cuts (default); director: tool-using agent")
    ap.add_argument("--no-ai", action="store_true", help="code only: quality filter + heuristic selector")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    code_only = {"analyzers": ["quality"], "selector": "heuristic"}
    config = code_only if a.no_ai else {"selector": a.selector,
                                        "llm": {"provider": a.provider, "model": a.model}}
    try:
        pipeline = build_pipeline(config)
    except Exception as e:  # noqa: BLE001 - e.g. SDK not installed or no API key
        print(f"AI unavailable ({type(e).__name__}: {e}); running code-only", file=sys.stderr)
        pipeline = build_pipeline(code_only)

    playbooks.set_user_dir(a.playbook_dir)
    try:
        custom = playbooks.load_file(a.playbook) if a.playbook else None
        brief = brief_from_prompt(a.prompt, a.duration or 0, a.genre or (custom or {}).get("genre"), a.music)
        if custom or a.overrides:
            brief.playbook = playbooks.apply_overrides(custom or playbooks.get_playbook(brief.genre), a.overrides)
        rules = playbooks.rules_for(brief)
    except playbooks.PlaybookError as e:
        print(f"playbook error: {e}", file=sys.stderr)
        return 2
    brief.title = a.title
    brief.duration = a.duration or rules.default_duration()
    if rules.length_min and rules.length_max and not rules.length_min <= brief.duration <= rules.length_max:
        print(f"warning: {brief.duration:.0f}s is outside the playbook's {rules.length_min}-{rules.length_max}s range",
              file=sys.stderr)
    out = a.out or str(Path(os.environ.get("REEL_OUTPUT_DIR", DEFAULT_OUTPUT_DIR))
                       / f"reel_{time.strftime('%Y%m%d_%H%M%S')}.mp4")
    Path(WORK_DIR).mkdir(parents=True, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix="reel_", dir=WORK_DIR)
    session, plan, result = pipeline.run(a.source, brief, workdir, out)

    source = a.playbook or next((str(d / f"{brief.genre}.json") for d in playbooks._dirs()
                                 if (d / f"{brief.genre}.json").is_file()), "?")
    print(f"brief: genre={brief.genre} pace={brief.pace} style={brief.style} target={brief.duration:.0f}s")
    print(f"playbook: {source}{' + ' + ', '.join(a.overrides) if a.overrides else ''}")
    print(f"rules: cut~{brief.shot_seconds}s max={rules.max_clip_seconds}s closing={rules.closing_seconds} "
          f"hard_cuts={rules.hard_cuts} text={rules.text_on}@{rules.text_position}")
    for e in pipeline.events:
        print("•", e)
    for s in session.shots:
        sc = session.scores[s.id]
        if sc.dropped:
            print(f"  drop {s.id}: {sc.dropped}")
    print(f"plan ({plan.selector}) {plan.title!r}:")
    assets = {x.id: x for x in session.assets}
    for n, it in enumerate(plan.items, 1):
        shot = session.shot(it.shot_id) if it.shot_id else None
        asset = assets[it.asset_id or shot.asset_id]
        start = it.start if it.start is not None else shot.start
        why = it.reason or (session.scores[it.shot_id].reason if it.shot_id else "")
        cap = f' "{it.caption}"' if it.caption else ""
        print(f"  {n:2}. {Path(asset.path).name} @ {start:.2f}s for {it.seconds:.2f}s{cap}  {why}")
    for note in plan.notes + session.notes:
        print("  note:", note)
    print(f"-> {result.path} ({result.width}x{result.height}, {result.seconds}s, {result.size_mb} MB); "
          f"report {Path(result.path).with_suffix('.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
