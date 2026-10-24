# Plugging in your own processing agents

Every step of the editing pipeline is a **slot**. Each slot runs the built-in implementation
unless you point it at your own agent — a Python class or an HTTP service — in configuration.
No code in the conversation, WhatsApp or job logic changes.

```
planning (before the user confirms)                    rendering (after "yes")
quality_filter → scene_split → moment_scoring          narration → captions → music → render
             → sequencing → script → music                                   → validation (always built-in)
```

| Step | Built-in implementation | Typical replacement |
|---|---|---|
| `quality_filter` | FFmpeg luma/motion/aHash: drops dark, shaky, near-duplicate clips | your own CV model |
| `scene_split` | FFmpeg scene-change detection, windows long shots, samples a frame per segment | shot-boundary model |
| `moment_scoring` | AI provider vision model (Gemini) scores frames vs. brief + playbook; heuristic fallback | your scoring/ranking agent |
| `sequencing` | playbook structure (hook → build → payoff), pacing, fit to target length | story/beat agent |
| `script` | AI provider writes narration per moment; template fallback | your copywriting agent |
| `narration` | ElevenLabs TTS with word timings (espeak/mock offline) | other TTS / cloned voice |
| `captions` | ASS captions from the script + word timings | styled caption agent |
| `music` | licensed library (`backend/music/library.yaml`), generated ambient bed fallback | music recommender |
| `render` | FFmpeg: 9:16 crop around subject, trims, photo motion, grade, transitions, title, captions, ducked mix, 16 MB compression | your renderer / GPU service |

The final output is **always** validated by the backend (9:16, duration, H.264/AAC, size) no
matter which renderer ran, and the job records which implementation ran each step
(`generation_jobs.pipeline`, shown on the project page).

## Configure

Environment (one variable per step):
```bash
PIPELINE_MOMENT_SCORING=https://agents.internal/score
PIPELINE_SCRIPT=python:my_agents.script:ScriptAgent
PIPELINE_HTTP_TOKEN=change-me            # sent as "Authorization: Bearer …" to HTTP agents
PIPELINE_FALLBACK_TO_BUILTIN=true        # if your agent fails, run the built-in step instead
```
or a YAML file (`PIPELINE_CONFIG_FILE=/app/pipeline.yaml`, see `backend/pipeline.example.yaml`):
```yaml
steps:
  moment_scoring: https://agents.internal/score
  render: python:my_agents.render:GpuRenderer
```
Env vars win over the file. Check what's active: `GET /api/dev/info` (dev) or the job's `pipeline` field.

## The contract

Inputs and outputs are Pydantic models in [`backend/app/pipeline/contracts.py`](../backend/app/pipeline/contracts.py)
(`CONTRACT_VERSION = "1"`). They are the single source of truth for both plugin and HTTP agents.

### Python plugin
```python
# my_agents/score.py  (install it into the image, e.g. COPY + pip install)
from app.pipeline.contracts import ScoringInput, ScoringOutput, SegmentScore

class MyScorer:
    def run(self, inp: ScoringInput) -> ScoringOutput:
        scores = [SegmentScore(segment_id=s.segment_id, score=7.5, description="…", reason="…")
                  for s in inp.segments]
        return ScoringOutput(scores=scores, scorer="my-scorer")
```
`PIPELINE_MOMENT_SCORING=python:my_agents.score:MyScorer`. Return either the model or a dict.

### HTTP service
ReelChat sends `POST <your url>`:
```json
{
  "step": "moment_scoring",
  "contract_version": "1",
  "input": {
    "segments": [{"segment_id": "a1:0", "asset_id": "a1", "kind": "video", "start": 0.0, "end": 3.2,
                  "frame_urls": ["https://<api>/api/pipeline-files/<signed-token>"]}],
    "context": {"genre": "travel", "vibe": "cinematic", "target_seconds": 30, "instructions": "start with the beach",
                "playbook": {"hook": {...}, "pacing": {...}, "selection_rules": {...}}, "...": "..."}
  }
}
```
Rules:
* Any input field ending in `_path`/`_paths` arrives as `_url`/`_urls`: a **signed link valid for 1 hour**
  (`GET` it to download the file). Local paths and work directories are never sent.
* Respond `200` with the output model as JSON. To return a file, give a `<name>_url` (e.g. `"video_url"`,
  `"audio_url"`, `"subtitles_url"`); ReelChat downloads it (max 500 MB) and fills `<name>_path`.
* `429`/`5xx`/network errors are **retried** with backoff (the job retries); other `4xx` or an invalid body fail
  the step (or fall back to built-in if enabled).
* Timeout: `PIPELINE_HTTP_TIMEOUT_SECONDS` (default 600).
* Authenticate requests with the `Authorization: Bearer $PIPELINE_HTTP_TOKEN` header. `PUBLIC_BASE_URL` must be
  reachable from your agent for file links.

Example response for `render`:
```json
{"video_url": "https://my-renderer/out/123.mp4", "duration": 29.6, "width": 720, "height": 1280, "size_bytes": 8123456}
```

### Step reference (abridged — see contracts.py for every field)

| Step | Input | Output |
|---|---|---|
| quality_filter | `assets[]` (file, kind, duration, size), `context` | `results[]`: asset_id, keep, score 0-1, flags, duplicate_of, reason |
| scene_split | `assets[]`, `context`, | `segments[]`: segment_id, asset_id, kind, start, end, frame_paths |
| moment_scoring | `segments[]`, `context` | `scores[]`: segment_id, score 0-10, description, tags, reason, focus_x 0-1; `scorer` |
| sequencing | `segments`, `scores`, `assets`, `context` | `items[]` (segment, source_start/end, role hook/build/payoff, reason, focus_x), `total_seconds`, `notes` |
| script | `items`, `total_seconds`, `context` | `title`, `lines[]` (item_index, text) |
| narration | `text`, `voice`, `vibe`, `language` | `audio_path`, `duration`, `words[]` (word, start, end), `provider`, `is_demo` |
| captions | `script`, `words`, `narration_offset`, `items`, `item_starts`, `total_seconds`, `position` | `subtitles_path` (ASS/SRT), `cues` |
| music | `moods`, `energy`, `seconds` | `audio_path`, `title`, `artist`, `license`, `source_url`, `generated` |
| render | `items`, `assets`, `context`, narration/subtitles/music paths, `title`, `width`, `height`, `target_bytes` | `video_path`, `duration`, `width`, `height`, `size_bytes` |

## Safety rules for agents
* Agents get only what their step needs, as data. The genre playbook is passed read-only.
* User text (`instructions`, captions, transcripts) is **untrusted** — don't let it drive tool use in your agent.
* Agents never get database access, credentials, or other users' files; signed links expire after an hour.
* Spending credits always requires the user's explicit confirmation in WhatsApp, which happens before any
  render-phase agent runs.
