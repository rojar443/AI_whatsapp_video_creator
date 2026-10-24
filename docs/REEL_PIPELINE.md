# Footage pipeline (Reel Director)

Turns the user's own videos and photos, plus a free-text request, into a 9:16 reel. Lives in `backend/app/reel/`.

```
request ──► Brief (genre, pace, style, duration)
files ──► Ingest ─► Sample ─► Quality filter ─► Selector ─────────────► FFmpeg render
          Asset[]   Shot[]    ShotScore[]       ReelPlan (timestamped)   MP4 + report.json
                                                  ▲
                                   vision model (Gemini by default)
```

## Default flow (Gemini)

1. **Request:** for example *"We went to Ladakh… I want a fast paced cinematic video"*. `brief_from_prompt` reads pace (fast → about 1.3s cuts), style and genre.
2. **Quality filter (code):** scene-splits each video and drops dark, blurry, shaky (frame jump in 0.2s) and duplicate shots.
3. **Timeline selector (model):** Gemini gets small, silent proxy copies of the **actual videos**, the photos, the request, the genre playbook and the ranges the filter rejected. It returns timestamped cuts in reel order: `{asset_id, start, end, caption, reason}`, a title and a summary. Uploads are deleted afterwards.
4. **Fit (code):** `fit_timeline` clamps cuts to the real footage, drops unknown or repeated moments, and trims everything to fit the target duration.
5. **Render (code):** FFmpeg cuts exactly those ranges, crops to 1080×1920, applies the genre grade, a push-in on photos and captions, and keeps the file under WhatsApp's 16 MB.

If the model is missing or fails, the heuristic selector produces the reel instead (fast pace splits long shots into quick cuts), so a reel is always delivered.

## Playbooks are user input

A playbook is a JSON file describing how a genre's reels are cut. Fields: `genre`, `hook`, `structure`, `pacing`, `length_seconds`, `visual_treatment`, `audio`, `selection_rules`, `confidence`. See `backend/playbooks/travel.json`. Where each run's playbook comes from, first match wins:

1. `--playbook FILE`: a file for this run only.
2. `--playbook-dir` / `$REEL_PLAYBOOK_DIR` (default `../../files/playbooks`): your playbooks, one `<genre>.json` each. A new file adds a new genre, and the request's wording selects it ("our wedding highlights" → `wedding.json`).
3. `backend/playbooks/`: bundled examples, used only as a fallback.

`--set key.path=value` overrides single values for one run, for example `--set pacing.avg_clip_seconds=1.2 --set visual_treatment.grade="cool moody"`. Files are read on every run, so edits apply immediately. `--title "LADAKH"` sets the hook text.

The whole playbook goes to the vision model. The rules code enforces are **derived from the playbook's wording** (`playbooks.rules()`), so any playbook in this format works without code changes:

| Rule | Read from | Example |
|---|---|---|
| average cut | `pacing.avg_clip_seconds` (or `cuts_per_10s`), adjusted by the requested pace | 1.5s, or 1.27s when "fast" |
| longest clip | "longer than N seconds" in `selection_rules.avoid` / `pace_notes` | 3s |
| closing shot | "closing … A-B seconds" in `pace_notes` | 2-3s |
| default length | middle of `length_seconds` | 20s |
| hard cuts | "hard cut" in `visual_treatment.transitions` | no fades |
| text | position, colour and hook-only vs every cut, from `text_style` and `hook` | white, upper third, opening only |
| colour grade | keywords in `visual_treatment.grade` (warm, cool, earthy, vibrant, moody, natural, cinematic, black and white) | warm + earthy + natural |

Explicit fields win over derived ones when present: `pacing.max_clip_seconds`, `pacing.closing_seconds`, `visual_treatment.ffmpeg_grade`.

## Swapping the vision model

All model calls go through `VisionLLM` (`llm.py`). Pick the vendor with `--provider` or `REEL_LLM_PROVIDER`, and the model with `--model` or `REEL_LLM_MODEL`.

| Provider | Default model | Input | Key |
|---|---|---|---|
| `gemini` (default) | `gemini-2.5-flash` | **whole videos** (native timestamps) + photos | `GEMINI_API_KEY` |
| `openai` | `gpt-5-mini` | frame strips per shot, labelled with time ranges | `OPENAI_API_KEY` |

Models with `supports_video = True` get the videos. Others automatically get the frame strips, and both return the same JSON, so nothing downstream changes. Adding a vendor means one class with `analyze_images` (and optionally `analyze_videos`) plus one line in `build_llm`.

Other selectors: `--selector director` (tool-using agent over the read-only tools in `tools.py`, OpenAI only) and `--selector heuristic` (code only).

## Run

```bash
cd backend
pip install -e ".[gemini]"          # or [openai]
export GEMINI_API_KEY=...
python -m app.reel --duration 20 \
  --prompt "We went to Ladakh, I have 3 videos and 3 photos. I want a fast paced cinematic video"
python -m app.reel --no-ai          # code only
```

Input: `$REEL_SOURCE_DIR` (default `../../files/input`). Output: `$REEL_OUTPUT_DIR` (default `../../files/output`) as `reel_<timestamp>.mp4` plus a `.json` report with every shot's metrics, drop reasons, the cuts with their reasons, and pipeline events. Working files go to `backend/var/reel-work/`.

## On hold / not yet

- **Sound:** ElevenLabs music or narration is on hold. `--music file.mp3` works today.
- Connecting it as a `VideoProvider` in the WhatsApp job flow, and WhatsApp video/image intake.
- An MCP server over `tools.py` for AgenticOrg.
