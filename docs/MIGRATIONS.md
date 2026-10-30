# Database migrations on any host

Run from `backend`, using the deployed backend's Python environment and the target
database's `DATABASE_URL`:

```sh
python -m app.migrate
python -m app.migrate --check
```

The first command runs Alembic's `upgrade head`. Alembic reads `alembic_version`
in that database and applies only pending revisions in dependency order. For a
database at `0002`, it applies `0003` followed by `0004`. A new database gets all
revisions. A current database needs no schema changes. Future committed revisions
are picked up by the same command; never hardcode revision numbers in startup.

The check command verifies the database matches the code's migration head without
applying changes. Both commands exit unsuccessfully on failure. The runner locates
the migration files relative to the backend package, rather than the working
directory. Keep `app/`, `alembic/`, and `alembic.ini` together in your deployment
artifact, as both existing Dockerfiles do.

## Render: already wired into startup

Keep the backend's existing Docker start command:

```sh
python -m app.render_runtime
```

That launcher runs `python -m app.migrate` before starting the API, worker,
scheduler, or Telegram poller. A migration failure stops startup. No additional
Render build script, per-migration command, or cron job is needed. Push the new
migration files and launcher changes to the backend's configured branch, then
deploy that commit. Deploying the frontend alone cannot migrate the database.

Successful backend logs end the migration phase with:

```text
Database schema is current (0004).
```

After deployment, the backend Shell can verify with
`python -m app.migrate --check`. The head number will advance as new migrations
are added. A dashboard command override must continue to use the launcher.

## Docker, another cloud, or a VM

The ordinary `backend/Dockerfile` also runs the same migrator before Uvicorn.
Keeping the image's default command preserves automatic migrations on another
Docker host. Compose waits for the API health check before starting its worker.

For a native Python deployment, use `backend` as the service working directory
and this POSIX start command (set `PORT` for that host):

```sh
python -m app.migrate && exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
```

For Windows PowerShell, fail before starting the API if migrations fail:

```powershell
python -m app.migrate
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Supply the same database URL to the migrator, API, and all workers. Use
`postgresql+psycopg://...` for PostgreSQL with this backend's installed driver.
The existing Render launcher converts Render's supplied PostgreSQL URL to that
format automatically. Local SQLite development is also supported.

If services are deployed independently, run migrations in a release job before
starting the new API and workers. PostgreSQL migration runs through this shared
runner take the same transaction lock, so concurrent runners wait rather than
apply the same revision simultaneously. This lock does not coordinate raw
`alembic upgrade` commands or stop an already running older worker. Use one
migrator for SQLite. Migrations must remain compatible with any older app
instances that stay running during a rolling deployment.

Keep the same database when moving hosts to preserve its data and migration
history. Using a new empty database creates the schema but does not copy existing
users or videos. Database migration credentials need permission to alter tables;
an application account without DDL privileges requires a separate release job.

## Adding the next revision

Commit a new Alembic migration with `down_revision` pointing to the previous
revision. Review its upgrade and downgrade operations, test it against an older
schema with representative data, and deploy it with the backend. Do not edit
already deployed migration files or use `alembic stamp head` to bypass pending
schema changes. Back up the production database before applying schema changes.
