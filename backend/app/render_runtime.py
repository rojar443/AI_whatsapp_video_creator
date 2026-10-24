"""Single-instance Render runtime sharing a persistent disk between all processes."""
from __future__ import annotations

import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

log = logging.getLogger(__name__)


def prepare_disk() -> None:
    root = Path(os.environ.get("RENDER_DATA_DIR", "/var/data"))
    if not os.path.ismount(root):
        raise RuntimeError("Persistent disk is not mounted at RENDER_DATA_DIR; refusing ephemeral media storage")
    paths = [root / name for name in ("storage", "work", "beat")]
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
    if os.geteuid() == 0:
        import pwd

        user = pwd.getpwnam("reelchat")
        for path in paths:
            os.chown(path, user.pw_uid, user.pw_gid)
        os.initgroups(user.pw_name, user.pw_gid)
        os.setgid(user.pw_gid)
        os.setuid(user.pw_uid)
        os.environ["HOME"] = user.pw_dir
    for path in paths:
        if not os.access(path, os.W_OK):
            raise RuntimeError(f"Persistent directory is not writable: {path}")
    os.environ.update(STORAGE_BACKEND="local", STORAGE_LOCAL_DIR=str(paths[0]),
                      WORK_DIR=str(paths[1]))


def commands() -> dict[str, list[str]]:
    python = sys.executable
    celery = [python, "-m", "celery", "-A", "app.jobs.celery_app"]
    data = Path(os.environ.get("RENDER_DATA_DIR", "/var/data"))
    result = {
        "api": [python, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0",
                "--port", os.environ.get("PORT", "10000"), "--proxy-headers",
                "--forwarded-allow-ips", "*"],
        "worker": celery + ["worker", "-Q", "default,media", "--concurrency", "1", "--loglevel", "INFO"],
        "beat": celery + ["beat", "--loglevel", "INFO", "--schedule", str(data / "beat/schedule")],
    }
    if os.environ.get("TELEGRAM_BOT_TOKEN", "").strip():
        result["telegram"] = [python, "-m", "app.telegram_poller"]
    return result


def supervise(process_commands: dict[str, list[str]], stop: threading.Event,
              shutdown_seconds: float = 25) -> int:
    """Exit the container if any child exits; terminate whole child process groups."""
    children: dict[str, subprocess.Popen] = {}
    result = 0
    try:
        for name, command in process_commands.items():
            if stop.is_set():
                break
            children[name] = subprocess.Popen(command, start_new_session=True)
            log.info("Started %s", name)
        while not stop.wait(0.25):
            for name, child in children.items():
                code = child.poll()
                if code is not None:
                    log.error("%s exited with code %s; restarting the service is required", name, code)
                    result = code if code > 0 else 1
                    stop.set()
                    break
    finally:
        for child in children.values():
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + shutdown_seconds
        for child in children.values():
            try:
                child.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
        # A Celery child can outlive its parent, so address every process group.
        for child in children.values():
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for child in children.values():
            child.wait()
    return result


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    prepare_disk()
    os.environ["TASK_BACKEND"] = "celery"
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
    # Fail before accepting requests if the queue or the media executables are unavailable.
    from redis import Redis
    from app.config import get_settings

    Redis.from_url(get_settings().redis_url, socket_connect_timeout=5, socket_timeout=5).ping()
    for executable in (get_settings().ffmpeg_path, get_settings().ffprobe_path):
        subprocess.run([executable, "-version"], check=True, stdout=subprocess.DEVNULL)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    return supervise(commands(), stop)


if __name__ == "__main__":
    raise SystemExit(main())
