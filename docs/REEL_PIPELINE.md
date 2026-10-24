# Footage pipeline (Reel Director)

Turns the user's own videos and photos into a 9:16 reel. Lives in `backend/app/reel/`.

```
Ingestor -> Sampler -> Analyzer(s) -> Selector -> Renderer
 Asset[]     Shot[]    ShotScore[]    ReelPlan    RenderResult (+ report.json)
```

| Stage | Module | Implementations | Code or model |
|---|---|---|---|
| Ingest | `ingest.py` | `FolderIngestor` (WhatsApp batch: later) | code |
| Sample | `sample.py` | `FFmpegSceneSampler`: scene cuts, shots of up to 6s, 3 frames + a frame strip per shot | code |
| Analyze | `analyze.py` | `QualityAnalyzer`: brightness, sharpness, motion, shake; drops dark, blurry, shaky and duplicate shots | code |
| | | `VisionAnalyzer`: one vision call scores every shot (hook, story, role, caption) against the playbook | model |
| Select | `select.py` | `HeuristicSelector` (offline and fallback), `DirectorSelector` (agent with tools) | code / agent |
| Render | `render.py` | `FFmpegRenderer`: crop, grade per genre, photo push-in, captions, music, fits 16 MB | code |

- `llm.py`: `VisionLLM` interface. `OpenAIVision` uses image inputs, strict JSON schemas and function calling. The model comes from `REEL_LLM_MODEL` (default `gpt-5-mini`) and the key from `OPENAI_API_KEY`.
- `tools.py`: **read-only** tools over a session: `list_shots`, `view_shots`, `get_playbook`, and `submit_plan`, which only checks a plan. The Director uses them, and they are also the surface to expose to an external orchestrator (for example AgenticOrg Remote MCP). None of them renders or writes.
- `playbooks.py`: genre playbooks (travel, daily). These are data the agent reads, not instructions it obeys.

If the vision model or the Director fails, the pipeline falls back to the heuristic selector, so a reel is always produced.

## Run

```bash
cd backend
python -m app.reel                     # uses $REEL_SOURCE_DIR or ../../files
python -m app.reel ./clips --prompt "cinematic travel reel" --duration 30 --music track.mp3
python -m app.reel --no-ai             # code only, no API key needed
```

Output: `var/reels/reel.mp4` plus `reel.json` (every shot's metrics, why it was dropped or kept, the plan and pipeline events).

## Not yet

- Connecting it as a `VideoProvider` in the WhatsApp job flow, and WhatsApp video/image intake
- An MCP server over `tools.py` for AgenticOrg
- Narration (TTS) from a script, and a vision review of the rendered reel
