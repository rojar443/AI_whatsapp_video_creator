# Playbook research plan (runs in parallel with the build)

Playbooks live in `backend/playbooks/<genre>.v<N>.yaml`, validated by `app/playbooks.py`. The shipped v1 files
are the **hypotheses** from the spec — they are not yet backed by research.

## Steps
1. Collect 30–50 strong reference reels per genre (travel, daily life) with a note on why each works.
2. Tag by hand: hook type (first 1–3 s), beat order, cut timestamps (→ avg clip length, cuts per 10 s), length,
   grade, transitions, text/caption placement, audio (music energy/tempo, narration, original sound).
   Spreadsheet columns = playbook fields.
3. Test whether a vision model can tag them reliably (compare against the hand tags) — if yes, use it to
   semi-automate refreshes.
4. Read the creator best-practice guidance Instagram and YouTube publish.
5. Write playbook v2 per genre; bump `version` (the loader logs an error if content changes without a bump, and
   every plan records `genre@version`).
6. A/B: edit the same raw footage with the playbook and with a neutral playbook (equal shares, no hook rules,
   median pacing); ask target users which version they'd post. Success metric in [PRD.md](PRD.md).
7. Review monthly or quarterly.

## Governance
* Files change only through reviewed pull requests; the agent receives playbooks as read-only data and has no
  tool to modify them.
* Never promise virality — the product promises reels structured the way strong reels in a genre are structured.

## Open questions
* Who curates: manual, or semi-automated with a model?
* How limited is the music library, and how well can it match genre audio guidance?
