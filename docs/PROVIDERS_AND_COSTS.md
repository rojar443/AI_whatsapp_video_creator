# Provider configuration & cost assumptions

All providers sit behind interfaces; nothing in the conversation or job logic names a vendor.

| Category | Interface | Implementations | Select with |
|---|---|---|---|
| AI agent | `app/agents/base.py:AgentProvider` | `rules` (offline, default/fallback), `anthropic` (initial production provider), `openai`, `openai_compatible` (Ollama, vLLM, LM Studio…), `gemini` | `AGENT_PROVIDER`, `AGENT_MODEL[_ANALYSIS/_RESPONSE/_SCRIPT]`, or **Admin → Providers** |
| Video | `app/video/base.py:VideoProvider` | `mock` (FFmpeg text cards, watermarked), `stock` (Pexels portrait footage + FFmpeg) | `VIDEO_PROVIDER` or Admin |
| Text-to-speech | `app/tts:TTSProvider` | `mock` (silence), `espeak` (free, offline, robotic), `openai` | `TTS_PROVIDER` or Admin |
| Speech-to-text | `app/transcription:TranscriptionProvider` | `mock` (fixture/simulator hint), `openai` | `TRANSCRIPTION_PROVIDER` or Admin |
| Messaging | `app/messaging/base.py:MessagingProvider` | `dev` (simulator), `meta` (WhatsApp Cloud API) | `MESSAGING_PROVIDER` |
| Storage | `app/storage:Storage` | `local`, `s3` (AWS S3, R2, MinIO, …) | `STORAGE_BACKEND` |

Admin overrides are stored in `provider_configurations` (names and model ids only — never keys) and take effect on
the next message/job. "Reset to env" removes the override.

## Agent tasks and suggested models

| Task | What it does | Calls per… | Suggested |
|---|---|---|---|
| `analysis` | intent + requirement extraction (structured JSON) | every inbound message | `claude-opus-5-5` (default); `claude-haiku-5-5` to cut cost |
| `response` | short clarifying reply for unclear messages | only "unclear" messages | same as analysis |
| `script` | narration script + scene keywords (structured JSON) | each generation | `claude-opus-5-5` |

The Anthropic adapter uses the official SDK with `output_config.format` (JSON schema), `effort: low`, and the
server-side refusal fallback (`fallbacks: "default"`). Any provider error or refusal falls back to the `rules`
agent for that message, so the chat keeps working during an outage.

### Switching providers

```bash
# .env
AGENT_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
AGENT_MODEL_ANALYSIS=claude-haiku-5-5     # optional per-task override

# Self-hosted (Ollama on the host)
AGENT_PROVIDER=openai_compatible
OPENAI_BASE_URL=http://host.docker.internal:11434/v1
AGENT_MODEL=llama3.1
```
Or in the website: **Admin → Providers → agent → anthropic → Save** (validated before it's applied, audited).

Adding a provider: subclass `LLMAgentProvider` and implement `_complete_json()` / `_complete_text()`, then
`providers.register_agent("name", factory)` — no other code changes.

## Cost assumptions (estimates — check current vendor pricing)

Per 30-second video, one user conversation of ~6 messages:

| Item | Assumption | Est. cost |
|---|---|---|
| Agent analysis (Claude Opus 5.5, $4 / $20 per M tokens) | 6 calls × ~1.2k in / 150 out | ≈ $0.047 |
| …same with Claude Haiku 5.5 ($0.10 / $0.50) | | ≈ $0.001 |
| Script (Claude Opus 5.5) | 1 call × ~600 in / 600 out | ≈ $0.014 |
| Speech-to-text (OpenAI mini transcribe) | 20 s voice note, ~$0.003/min | ≈ $0.001 |
| TTS (OpenAI mini TTS) | ~75 words narration | ≈ $0.002–0.01 |
| Stock footage (Pexels) | free, attribution recommended | $0 |
| Generative video API (if added) | typically $0.05–0.50 per second of output | $1.50–15 |
| FFmpeg render | ~10–40 s CPU on a 2 vCPU worker | ≈ $0.001 |
| Storage + egress | 5–10 MB per video, 30-day retention | < $0.001 |
| WhatsApp | user-initiated service conversations: free within 24 h; utility templates outside the window are billed per message by country | $0–0.03 |

So the stock-footage pipeline costs roughly **$0.02–0.08 per video**, dominated by the LLM choice. Credit pricing
(1 credit per 15 s, ×2 for high quality) leaves headroom; adjust in `requirements.credits_for()`.

The admin **Usage & cost** tab sums `usage_records.estimated_cost_usd`; LLM prices live in
`app/services/credits.py:LLM_PRICE_PER_MTOK` — edit them to match your contracts.

## Provider capability notes

* `mock` video: all styles, standard (720×1280) and high (1080×1920). Visuals are coloured text cards.
* `stock`: no `animation` style (falls back to cinematic, and the user is told before confirming).
* Languages are limited by the TTS provider (`espeak`: en, hi, ml, ta, es, fr). With the `rules` agent the script
  is written in English from templates; translated narration needs an LLM agent.
* Logos are not supported yet; the agent says so when asked. A call to action is woven into the script. Brand colours drive the palette of the `mock` text-card visuals; the `stock` provider ignores them.
* Music is synthesised procedurally (royalty-free by construction). Commercial songs are refused.
