"""Footage pipeline: the user's own clips and photos -> analysed, sequenced 9:16 reel.

Stages are small, swappable modules that exchange the data objects in app.reel.models:

    Ingestor -> Sampler -> Analyzer(s) -> Selector -> Renderer
     Asset[]     Shot[]    ShotScore[]    ReelPlan    RenderResult

Deterministic work (sampling, quality filter, rendering) is plain code. Judgement
(what is in a shot, which shots open/close the reel) goes through a VisionLLM, which
an external orchestrator can replace. The read-only functions in app.reel.tools are
what an agent or orchestrator calls to inspect a session.

This package deliberately depends only on ffmpeg, numpy and Pillow (plus an optional
LLM SDK), so it runs from the command line without the web stack:

    python -m app.reel ./clips --prompt "cinematic travel reel" --duration 30
"""
