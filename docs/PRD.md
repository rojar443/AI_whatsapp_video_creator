# ReelChat — product requirements (MVP)

## Vision
Turn the raw photos and videos on people's phones into polished vertical reels through a WhatsApp
conversation. No app to install, no editing to learn: describe the vibe, check the plan, get the reel.

## Problem
People capture lots of footage but rarely post it. Editing takes hours, tools are hard to learn, people don't know
what makes a reel work, and styles like "cinematic" need real skill. Footage stays unused.

## Users
* **Traveller** with 5–20 clips from a trip who wants a cinematic reel to post the same evening.
* **Everyday creator** documenting a day (food, family, routines) who wants a relatable "day in my life" reel,
  ideally with narration.

## User stories (MVP)
1. As a new user I can send clips and photos to the WhatsApp number and get a first draft without registering.
2. I can describe what I want by text or voice note; I'm only asked what's essential (the genre).
3. Before anything is made I see a plan: length, look, narration, music, which moments were picked and why,
   what was skipped, and the cost.
4. After I confirm, I get progress messages and the reel (or a secure link) in the same chat.
5. I can ask for revisions ("shorter", "start with the beach", "no music").
6. I can register on the website, link my number, and see my reels and their history.
7. If processing fails I'm refunded and can retry without being charged twice.

## WhatsApp flows
* **Intake:** each clip/photo is stored; one debounced acknowledgement per burst; the batch closes on
  "that's all"/"done" or after 90 s of silence.
* **Preferences:** genre (travel / daily life) is the only required question (buttons); everything else has
  playbook defaults and can be stated in free text.
* **Plan → confirm:** plan message with ✅ Proceed / ✏️ Change / ✖ Cancel; only an explicit yes or the
  current plan's button starts paid work.
* **Delivery:** MP4 attachment ≤16 MB, otherwise a signed link; outside the 24-hour window an approved template is
  sent first and the reel is released when the user replies.
* **Scope:** the bot only makes reels and declines open-ended chat (Meta AI chatbot policy, 15 Jan 2026).

## Limits (configurable)
20 files per reel · 100 MB per clip · 180 s per clip · 50 MP images · 15/30/60 s or original length (≤90 s) ·
anonymous: 2 credits, 2 projects, 3-day retention · registered: 10 credits, 10 reels/day, 30-day retention ·
source media deleted after 7 days.

## Risks
| Risk | Mitigation |
|---|---|
| Meta policy changes / number restrictions | narrow scope, templates, recheck terms before launch |
| WhatsApp-compressed source quality | ask users to send "as document"; test with real phones |
| Vision model mis-picks moments | plan shown before render; revisions; playbook rules; heuristic fallback |
| Cost of vision + TTS per reel | low-res single frame per segment, batching, per-user quotas |
| Voice cloning misuse (phase 2) | mandatory recorded consent, deletion, provider terms check |
| Music licensing | only manifest-listed licensed tracks; generated bed fallback |

## Success metric
Share of first drafts the user approves without more than one revision, and — for the playbook — test users
prefer the playbook-edited version of the same footage over the unguided edit (A/B on the same clips).

## External accounts & paid services
| Service | Needed for | Cost model |
|---|---|---|
| Meta developer app + WhatsApp Business Platform | messaging | free test number; conversation/template pricing by country in production |
| Google Gemini API | chat analysis, voice transcription, vision scoring, script | per token |
| ElevenLabs | narration (phase 2: voice cloning — check plan limits for many end-user voices) | per character / plan |
| Cloudflare (Tunnel) | public HTTPS webhook in development | free |
| S3-compatible storage | production media storage | per GB |
| Licensed music source | background music | per licence |
