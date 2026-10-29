"""Deployment contract tests; no live cloud resources or credentials required."""
from configparser import ConfigParser
from pathlib import Path
import shlex

import pytest

from app.render_runtime import prepare_disk, supervisor_config


def test_processes_restart_independently_and_stop_intake_first(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("PORT", "12345")
    monkeypatch.setenv("WORKER_CONCURRENCY", "1")
    config = ConfigParser()
    config.read_string(supervisor_config(Path("/var/data")))
    names = {s for s in config.sections() if s.startswith("program:")}
    assert names == {"program:api", "program:worker", "program:scheduler", "program:telegram"}
    for name in names:
        assert config[name].getboolean("autorestart")
        assert config[name].getboolean("killasgroup")
        assert shlex.split(config[name]["command"])
    assert "--port 12345" in config["program:api"]["command"]
    assert "-B" not in shlex.split(config["program:worker"]["command"])
    assert config["program:telegram"].getint("priority") > config["program:worker"].getint("priority")
    assert "/var/data/beat/schedule" in config["program:scheduler"]["command"].replace("\\", "/")
    assert "test-token" not in supervisor_config(Path("/var/data"))


def test_telegram_is_optional(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert "[program:telegram]" not in supervisor_config(Path("/var/data"))


@pytest.mark.parametrize("port", ["0", "65536", "not-a-port"])
def test_invalid_port_rejected(monkeypatch, port):
    monkeypatch.setenv("PORT", port)
    with pytest.raises(ValueError):
        supervisor_config(Path("/var/data"))


def test_refuses_ephemeral_media(monkeypatch, tmp_path):
    monkeypatch.setenv("RENDER_DATA_DIR", str(tmp_path / "not-mounted"))
    monkeypatch.setattr("os.path.ismount", lambda _: False)
    with pytest.raises(RuntimeError, match="Persistent disk missing"):
        prepare_disk()
    assert not (tmp_path / "not-mounted").exists()
