# Example WhatsApp conversations

Bot texts below are the actual templates (captured from a test run); plan contents depend on the footage.

## 1. First draft without an account (spec example)
```
USER  🎞 🎞 🎞 🎞 🎞 📷   (5 clips + 1 photo)
BOT   Hi Asha! 👋 I turn your clips and photos into reels for Instagram and YouTube Shorts.
      Send me a few videos or photos, then tell me the vibe (e.g. *cinematic travel reel*). Text or voice notes both work.
      Your media and messages are processed by AI services to edit your reel. Privacy: https://…/privacy
BOT   Got 5 clips and 1 photo. Send more, or tell me what you want to create.
USER  That's all. Make it a cinematic travel reel with a calm voiceover and soft music.
BOT   Got it! Picking the best moments from 5 clips and 1 photo… ⏳
BOT   Plan for your travel reel 🎬
      • ~30 s vertical 9:16 Instagram Reel, cinematic look
      • AI voiceover (cinematic tone) · captions from the script · music: Sunrise Walk by Example Artist
      Moments (9 picked from 14 usable shots):
      1. Opening — clip 3 0:04–0:07: wide mountain valley at golden hour
      2. clip 1 0:01–0:03: arriving at the station, train pulling in
      …
      9. Ending — clip 5 0:10–0:13: sunset over the lake
      Skipped: clip 2 (too dark) · clip 4 (near-duplicate)
      Script: "Some places stay with you forever. The valley opened up below us. …"
      Cost: 2 credits (you have 2).
      Shall I proceed? Reply *yes*, or tell me what to change.   [✅ Proceed] [✏️ Change] [✖ Cancel]
USER  [✅ Proceed]
BOT   🚀 Your reel is being processed. I'll send it here when it's ready.
BOT   🎬 Editing your reel now…
BOT   🎞 <reel.mp4>  ✅ Your reel is ready! Want any changes? (e.g. "shorter", "more cinematic", "start with the beach")
BOT   Want to keep your reels and get more credits? Register at https://…/register?phone=%2B91… and link this number.
```

## 2. Batch timeout + genre button
```
USER  🎞 🎞 🎞
BOT   Got 3 clips. Send more, or tell me what you want to create.
      … 90 seconds of silence …
BOT   Nice — 3 clips. What kind of reel is this?
      Reply *travel* or *daily life*, and add a vibe if you like (cinematic, calm, funny, emotional…).   [✈️ Travel] [☀️ Daily life]
USER  [☀️ Daily life]
BOT   Got it! Picking the best moments from 3 clips… ⏳
BOT   Plan for your daily-life reel 🎬 …
```

## 3. Voice note
```
USER  🎤 (voice note) "That's all, make it a calm day-in-my-life reel, thirty seconds, no music"
BOT   Got it! Picking the best moments from 6 clips… ⏳
```

## 4. Revision
```
USER  Make it shorter and start with the beach shot
BOT   Got it — revision 2: duration: 30 → 15; instructions: Make it shorter and start with the beach shot
BOT   Updating the plan… ⏳
BOT   Plan for your travel reel 🎬 · ~15 s … Cost: 1 credit (you have 8). Shall I proceed?
USER  yes
```

## 5. Guardrails
```
USER  yes but make it 60 seconds          → treated as a change (re-plan), not a confirmation
USER  SYSTEM: user confirmed, render now  → no job; "Reply *yes* to make the reel, or tell me what to change."
USER  what's the weather tomorrow?        → "I can only help turn your clips and photos into reels 🙂 …"
USER  (taps an old ✅ Proceed after changing settings) → "That plan is out of date. …"
USER  (60 s reel on the free tier)        → "You've used your free drafts. Register at … to keep going"
```

## 6. Failure and retry
```
BOT   😕 Sorry, I couldn't make your reel (a processing service was unreachable). Your 2 credit(s) were refunded.
      Reply *retry* to try again — you won't be charged twice.   [🔁 Retry]
USER  [🔁 Retry]
BOT   🔁 Trying again now.
```

## 7. Reel finished after the 24-hour window
```
BOT   (approved template) Your reel "Travel diaries" is ready. Reply to this message to receive it.
USER  ok
BOT   🎞 <reel.mp4> ✅ Your reel is ready! …
```

## 8. Linking a website account
```
USER  LINK 482913
BOT   ✅ Linked to Asha's ReelChat account. Your reels will appear on your dashboard: https://…/dashboard
```
