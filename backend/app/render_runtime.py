"""Render disk bootstrap and independent process supervision (Linux only)."""
from __future__ import annotations

import os
import stat
from pathlib import Path
import subprocess
import sys


def prepare_disk() -> Path:
    root = Path(os.environ.get("RENDER_DATA_DIR", "/var/data")).resolve()
    if not os.path.ismount(root):
        raise RuntimeError("Persistent disk missing at RENDER_DATA_DIR; refusing ephemeral media storage")
    paths = [root / name for name in ("storage", "work", "beat")]
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
    if os.geteuid() == 0:
        import pwd

        user = pwd.getpwnam("reelchat")
        # Existing files already belong to this stable UID; avoid recursively touching user media.
        for path in paths:
            os.chown(path, user.pw_uid, user.pw_gid)
        # Docker creates stdout/stderr pipes as root. Supervisor reopens these
        # through /dev/stdout after dropping privileges, unlike a direct print.
        for fd in (1, 2):
            if stat.S_ISFIFO(os.fstat(fd).st_mode):
                os.fchown(fd, user.pw_uid, user.pw_gid)
        os.initgroups(user.pw_name, user.pw_gid)
        os.setgid(user.pw_gid)
        os.setuid(user.pw_uid)
        os.environ["HOME"] = user.pw_dir
    for path in paths:
        if not os.access(path, os.W_OK):
            raise RuntimeError(f"Persistent directory is not writable: {path}")
    os.environ.update(STORAGE_BACKEND="local", STORAGE_LOCAL_DIR=str(paths[0]),
                      WORK_DIR=str(paths[1]), TASK_BACKEND="celery")
    return root


def supervisor_config(root: Path) -> str:
    port = int(os.environ.get("PORT", "10000"))
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be between 1 and 65535")
    concurrency = int(os.environ.get("WORKER_CONCURRENCY", "1"))
    if concurrency < 1:
        raise ValueError("WORKER_CONCURRENCY must be positive")
    python = sys.executable
    celery = f"{python} -m celery -A app.jobs.celery_app"
    # Lower priorities start first and stop last. Stop intake before draining the worker.
    programs = {
        "worker": (10, f"{celery} worker -Q default,media --concurrency {concurrency} --loglevel INFO"),
        "api": (20, f"{python} -m uvicorn app.main:app --host 0.0.0.0 --port {port} "
                       "--proxy-headers --forwarded-allow-ips '*'"),
        "scheduler": (30, f'{celery} beat --loglevel INFO --schedule "{root / "beat/schedule"}"'),
    }
    if os.environ.get("TELEGRAM_BOT_TOKEN", "").strip():
        programs["telegram"] = (30, f"{python} -m app.telegram_poller")
    config = """[unix_http_server]
file=/tmp/reelchat-supervisor.sock
chmod=0600
[supervisord]
nodaemon=true
logfile=/dev/stdout
logfile_maxbytes=0
pidfile=/tmp/reelchat-supervisord.pid
childlogdir=/tmp
[rpcinterface:supervisor]
supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface
[supervisorctl]
serverurl=unix:///tmp/reelchat-supervisor.sock
"""
    for name, (priority, command) in programs.items():
        # Supervisor interpolates percent signs even inside command values.
        command = command.replace("%", "%%")
        # Disk-backed Render services have a fixed shutdown window. Three stop tiers
        # take at most 5 + 5 + 12 seconds, leaving time before the platform kill.
        stop_seconds = 12 if name == "worker" else 5
        config += f"""
[program:{name}]
command={command}
directory=/app
priority={priority}
autostart=true
autorestart=true
startsecs=3
startretries=20
stopsignal=TERM
stopasgroup=true
killasgroup=true
stopwaitsecs={stop_seconds}
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
redirect_stderr=true
"""
    return config


def main() -> None:
    root = prepare_disk()
    # Render's PostgreSQL URL may omit the installed psycopg3 driver name.
    from sqlalchemy.engine import make_url

    url = make_url(os.environ["DATABASE_URL"])
    if url.get_backend_name() != "postgresql":
        raise RuntimeError("Render requires a PostgreSQL DATABASE_URL")
    os.environ["DATABASE_URL"] = url.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)
    from app.config import get_settings
    from redis import Redis

    settings = get_settings()
    if settings.app_env != "production" or settings.secret_key.startswith("dev-"):
        raise RuntimeError("Set APP_ENV=production and a production SECRET_KEY")
    # Fail startup before any process can consume jobs against an old schema.
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
    Redis.from_url(settings.redis_url, socket_connect_timeout=5, socket_timeout=5).ping()
    for executable in (settings.ffmpeg_path, settings.ffprobe_path):
        subprocess.run([executable, "-version"], check=True, stdout=subprocess.DEVNULL)
    config_path = Path("/tmp/reelchat-supervisord.conf")
    config_path.write_text(supervisor_config(root))
    os.execvp("supervisord", ["supervisord", "-c", str(config_path)])


if __name__ == "__main__":
    main()
