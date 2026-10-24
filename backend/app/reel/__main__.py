"""Command line: python -m app.reel CLIPS_DIR [--prompt ...] [--duration 30] [--no-ai]"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from pathlib import Path

from app.reel.models import Brief
from app.reel.pipeline import build_pipeline
from app.reel.playbooks import PLAYBOOKS

DEFAULT_SOURCE = "../../files"  # /work/Hackathon/files when run from backend/


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.reel", description="Clips + photos -> 9:16 reel")
    ap.add_argument("source", nargs="?", default=os.environ.get("REEL_SOURCE_DIR", DEFAULT_SOURCE),
                    help=f"folder of videos and photos (default: $REEL_SOURCE_DIR or {DEFAULT_SOURCE})")
    ap.add_argument("--prompt", default="cinematic travel reel")
    ap.add_argument("--genre", choices=sorted(PLAYBOOKS), default="travel")
    ap.add_argument("--duration", type=float, default=30)
    ap.add_argument("--music", help="optional music file")
    ap.add_argument("--out", default="var/reels/reel.mp4")
    ap.add_argument("--no-ai", action="store_true", help="code only: quality filter + heuristic selector")
    ap.add_argument("--model", help="vision model (default: $REEL_LLM_MODEL or gpt-5-mini)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    config = ({"analyzers": ["quality"], "selector": "heuristic"} if a.no_ai
              else {"llm": {"provider": "openai", "model": a.model}})
    try:
        pipeline = build_pipeline(config)
    except Exception as e:  # noqa: BLE001 - e.g. openai not installed or no key
        print(f"AI unavailable ({type(e).__name__}: {e}); running code-only", file=sys.stderr)
        pipeline = build_pipeline({"analyzers": ["quality"], "selector": "heuristic"})

    brief = Brief(a.prompt, a.genre, a.duration, a.music)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix="reel_", dir=str(Path(a.out).parent))
    session, plan, result = pipeline.run(a.source, brief, workdir, a.out)

    for e in pipeline.events:
        print("•", e)
    for s in session.shots:
        sc = session.scores[s.id]
        if sc.dropped:
            print(f"  drop {s.id}: {sc.dropped}")
    print(f"plan ({plan.selector}) {plan.title!r}:")
    assets = {x.id: x for x in session.assets}
    for n, it in enumerate(plan.items, 1):
        s, sc = session.shot(it.shot_id), session.scores[it.shot_id]
        print(f"  {n:2}. {it.shot_id} {Path(assets[s.asset_id].path).name}@{s.start}s {it.seconds}s "
              f"[{sc.role or '-'}] {sc.reason or ''}")
    for note in plan.notes + session.notes:
        print("  note:", note)
    print(f"-> {result.path} ({result.width}x{result.height}, {result.seconds}s, {result.size_mb} MB); "
          f"report {Path(result.path).with_suffix('.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
