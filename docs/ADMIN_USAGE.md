# Admin usage

Click a person's name in `/admin` to open `/admin/users/[userId]`.
The page shows all-time inbound message counts, provider-reported language-model
tokens, estimated costs, and one row per project. Expand a row for revision/task,
provider/model, input/output tokens, and model-call counts. Actual retry calls
contribute to the same project and revision totals.

`GET /api/admin/users/{user_id}/usage?limit=25&offset=0` requires the admin role.
Its response is an explicit metadata allowlist. It excludes project titles,
messages, transcripts, prompts, scripts, errors, asset identifiers, storage paths,
thumbnails, and media/download links. The existing project and media-link APIs
continue to require ownership; the admin role grants no exception.

Apply the database migration before starting the updated backend:

```powershell
cd backend
.\.venv\Scripts\python.exe -m alembic upgrade head
```

Migration `0003` links existing job-associated usage to its project/revision.
Other historical usage remains under Other / unattributed usage. Old input/output
splits and message/revision links cannot be reconstructed reliably. Historical
estimates retain their original precision and pricing; new token costs retain
ten decimal places. Unknown model prices are marked as incomplete, not free.

Conversation analysis, Gemini voice-note transcription, planning, and render-time
model calls record project/revision attribution. Within worker execution,
provider-reported usage is persisted after business transactions finish, including
handled failures and retries. A process crash before that final ledger write or a
provider response without usage can still leave a gap; these totals are an
operational estimate, not a replacement for provider billing. No historical
pricing is recomputed when the configured price table changes.
