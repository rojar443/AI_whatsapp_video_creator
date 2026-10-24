"""Deployment regressions: provider URL compatibility, readiness and process shutdown."""
import signal
import threading
from unittest.mock import Mock

import pytest

from app.config import Settings
from app import render_runtime


@pytest.mark.parametrize("scheme", ["postgres", "postgresql", "postgresql+psycopg"])
def test_render_database_url_uses_installed_driver(scheme):
    url = f"{scheme}://user:encoded%25password@host:5432/reelchat?sslmode=require"
    settings = Settings(_env_file=None, database_url=url)
    assert settings.database_url == "postgresql+psycopg://user:encoded%25password@host:5432/reelchat?sslmode=require"


def test_health_fails_readiness_when_database_is_down(client, monkeypatch):
    import app.main

    engine = Mock()
    engine.connect.side_effect = RuntimeError("database unavailable")
    monkeypatch.setattr(app.main, "get_engine", lambda: engine)
    response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json()["db"] is False


def test_runtime_rejects_missing_disk(monkeypatch, tmp_path):
    monkeypatch.setenv("RENDER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(render_runtime.os.path, "ismount", lambda _: False)
    with pytest.raises(RuntimeError, match="Persistent disk is not mounted"):
        render_runtime.prepare_disk()


def test_runtime_terminates_process_groups_if_a_child_exits(monkeypatch):
    api, worker = Mock(pid=101), Mock(pid=102)
    api.poll.return_value = None
    worker.poll.return_value = 7
    spawn = Mock(side_effect=[api, worker])
    kill = Mock()
    monkeypatch.setattr(render_runtime.subprocess, "Popen", spawn)
    monkeypatch.setattr(render_runtime.os, "killpg", kill, raising=False)
    # SIGKILL is not defined on Windows; these mocks exercise Linux group cleanup.
    monkeypatch.setattr(render_runtime.signal, "SIGKILL", 9, raising=False)
    assert render_runtime.supervise({"api": ["api"], "worker": ["worker"]}, threading.Event()) == 7
    assert (101, signal.SIGTERM) in [call.args for call in kill.call_args_list]
    assert (102, signal.SIGTERM) in [call.args for call in kill.call_args_list]
    assert (102, 9) in [call.args for call in kill.call_args_list]
    api.wait.assert_called()
    worker.wait.assert_called()


def test_runtime_cleans_up_after_partial_startup(monkeypatch):
    child = Mock(pid=101)
    monkeypatch.setattr(render_runtime.subprocess, "Popen", Mock(side_effect=[child, OSError("spawn failed")]))
    kill = Mock()
    monkeypatch.setattr(render_runtime.os, "killpg", kill, raising=False)
    monkeypatch.setattr(render_runtime.signal, "SIGKILL", 9, raising=False)
    with pytest.raises(OSError, match="spawn failed"):
        render_runtime.supervise({"api": ["api"], "worker": ["worker"]}, threading.Event())
    kill.assert_any_call(101, signal.SIGTERM)
    child.wait.assert_called()
