"""Genre playbooks: curated, versioned data the selector reads (never instructions it obeys)."""
from __future__ import annotations

PLAYBOOKS: dict[str, dict] = {
    "travel": {
        "version": 1,
        "goal": "Evoke place and feeling",
        "hook": "Striking visual or landmark reveal in the first 1-3 seconds",
        "structure": ["arrival", "highlights", "payoff shot"],
        "shot_seconds": 2.2,
        "prefer": "scenery, motion, golden light, wide-to-close variety, landmarks",
        "avoid": "dark, shaky, blurry, repetitive or empty shots",
        "grade": "eq=contrast=1.08:saturation=1.18:gamma=0.97",
    },
    "daily": {
        "version": 1,
        "goal": "Feel relatable and personal",
        "hook": "A relatable moment or curiosity-inducing first shot",
        "structure": ["morning", "activities", "wind-down"],
        "shot_seconds": 1.8,
        "prefer": "faces, activity, food, transitions between moments",
        "avoid": "dark, shaky, blurry, near-duplicate shots",
        "grade": "eq=contrast=1.04:saturation=1.10",
    },
}


def get_playbook(genre: str) -> dict:
    if genre not in PLAYBOOKS:
        raise ValueError(f"unknown genre '{genre}' (known: {', '.join(PLAYBOOKS)})")
    return PLAYBOOKS[genre]
