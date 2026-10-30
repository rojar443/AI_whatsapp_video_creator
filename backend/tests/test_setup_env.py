"""deploy/setup-env.sh, run for real in a scratch copy of the project with fake curl/docker."""
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "setup-env.sh"
EXAMPLE = ROOT / ".env.production.example"
TOKEN = "123456789:AAbb_cc-DD"
TOOLS = ["bash", "cat", "chmod", "cp", "cut", "date", "grep", "mktemp", "mv", "openssl", "rm", "tail", "dirname"]

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not installed")


def _exe(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def proj(tmp_path):
    (tmp_path / "deploy").mkdir()
    shutil.copy(SCRIPT, tmp_path / "deploy" / "setup-env.sh")
    shutil.copy(EXAMPLE, tmp_path / ".env.production.example")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for tool in TOOLS:  # only these tools are on PATH, so a real docker or curl is never used
        os.symlink(shutil.which(tool), bin_ / tool)
    _exe(bin_ / "curl", 'echo 198.51.100.9\n')
    return tmp_path


def docker(proj, ok=True, err=""):
    _exe(proj / "bin" / "docker", f'echo "{err}" >&2\nexit {0 if ok else 1}\n')


def run(proj, *answers):
    """Answers in prompt order: token, bot username, AI key, IP, domain."""
    return subprocess.run(["bash", "deploy/setup-env.sh"], cwd=proj, input="\n".join(answers) + "\n", text=True,
                          capture_output=True, env={"PATH": str(proj / "bin"), "HOME": str(proj)}, timeout=30)


def env(proj) -> dict:
    out = {}
    for line in (proj / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k] = v  # last wins, like docker compose
    return out


def leftovers(proj):
    return sorted(p.name for p in proj.iterdir() if p.name.startswith(".env.new") or p.name.endswith(".tmp"))


def backups(proj):
    return sorted(p.name for p in proj.iterdir() if p.name.startswith(".env.bak."))


# --------------------------------------------------------------------------- fresh install
def test_fresh_install_writes_complete_env(proj):
    r = run(proj, TOKEN, "MyBot", "ai-key", "203.0.113.7", "")
    assert r.returncode == 0, r.stderr
    e = env(proj)
    assert len(e["SECRET_KEY"]) == 64 and len(e["POSTGRES_PASSWORD"]) == 32
    assert e["TELEGRAM_BOT_TOKEN"] == TOKEN and e["TELEGRAM_BOT_USERNAME"] == "MyBot" and e["OPENAI_API_KEY"] == "ai-key"
    assert e["SITE_ADDRESS"] == "203-0-113-7.sslip.io" and e["SITE_IP"] == "203.0.113.7"
    assert e["WEB_BASE_URL"] == e["PUBLIC_BASE_URL"] == "https://203-0-113-7.sslip.io"
    assert e["CORS_ORIGINS"] == "https://203-0-113-7.sslip.io,https://203.0.113.7"
    assert stat.S_IMODE((proj / ".env").stat().st_mode) == 0o600
    assert backups(proj) == [] and leftovers(proj) == []


def test_example_address_never_leaks(proj):
    run(proj, TOKEN, "", "k", "203.0.113.7", "")
    assert "44-251-135-237" not in (proj / ".env").read_text().split("SITE_ADDRESS=")[-1]
    assert all("44.251.135.237" not in v and "44-251-135-237" not in v for v in env(proj).values())


def test_secrets_are_not_printed(proj):
    r = run(proj, TOKEN, "", "super-secret-ai-key", "203.0.113.7", "")
    e = env(proj)
    for k in ("SECRET_KEY", "POSTGRES_PASSWORD", "TELEGRAM_BOT_TOKEN", "OPENAI_API_KEY"):
        assert e[k] not in r.stdout
    assert "set (64 characters)" in r.stdout


def test_default_bot_username(proj):
    run(proj, TOKEN, "", "k", "203.0.113.7", "")
    assert env(proj)["TELEGRAM_BOT_USERNAME"] == "Chat2ReelBot"


def test_detected_ip_is_the_default(proj):
    r = run(proj, TOKEN, "", "k", "", "")
    assert r.returncode == 0
    assert env(proj)["SITE_IP"] == "198.51.100.9" and env(proj)["SITE_ADDRESS"] == "198-51-100-9.sslip.io"


def test_custom_domain(proj):
    run(proj, TOKEN, "", "k", "203.0.113.7", "reels.example.com")
    e = env(proj)
    assert e["SITE_ADDRESS"] == "reels.example.com" and e["WEB_BASE_URL"] == "https://reels.example.com"
    assert e["CORS_ORIGINS"] == "https://reels.example.com,https://203.0.113.7"


def test_token_with_special_characters_is_stored_verbatim(proj):
    run(proj, "987654321:A-_b", "", "k&|\\x/y", "203.0.113.7", "")  # characters that break a sed-based setter
    assert env(proj)["OPENAI_API_KEY"] == "k&|\\x/y"


# --------------------------------------------------------------------------- bad input never touches .env
@pytest.mark.parametrize("answers,message", [
    (("not-a-token", "", "k", "203.0.113.7", ""), "Telegram bot token"),
    (("123:", "", "k", "203.0.113.7", ""), "Telegram bot token"),
    ((TOKEN, "", "", "203.0.113.7", ""), "AI key is empty"),
    ((TOKEN, "", "k", "nope", ""), "IPv4"),
    ((TOKEN, "", "k", "1.2.3", ""), "IPv4"),
    ((TOKEN, "", "k", "203.0.113.7", "https://example.com"), "hostname"),
    ((TOKEN, "", "k", "203.0.113.7", "example.com/path"), "hostname"),
])
def test_bad_input_leaves_existing_env_untouched(proj, answers, message):
    run(proj, TOKEN, "", "good-key", "203.0.113.7", "")
    before = (proj / ".env").read_bytes()
    r = run(proj, *answers)
    assert r.returncode == 1 and message in r.stdout and ".env was not changed" in r.stdout
    assert (proj / ".env").read_bytes() == before
    assert backups(proj) == [] and leftovers(proj) == []


def test_bad_input_on_fresh_install_creates_nothing(proj):
    assert run(proj, "bad", "", "k", "203.0.113.7", "").returncode == 1
    assert not (proj / ".env").exists() and leftovers(proj) == []


def test_no_ip_detected_and_none_given_fails(proj):
    _exe(proj / "bin" / "curl", "exit 7\n")
    r = run(proj, TOKEN, "", "k", "", "")
    assert r.returncode == 1 and "IPv4" in r.stdout and not (proj / ".env").exists()


def test_must_run_inside_the_project(proj):
    (proj / ".env.production.example").unlink()
    r = run(proj, TOKEN, "", "k", "203.0.113.7", "")
    assert r.returncode == 1 and "not found" in r.stdout


# --------------------------------------------------------------------------- re-running on a live server
def test_rerun_keeps_secrets_and_updates_keys(proj):
    run(proj, TOKEN, "", "old-key", "203.0.113.7", "")
    first = env(proj)
    r = run(proj, "111:new_token", "OtherBot", "new-key", "", "")
    assert r.returncode == 0, r.stderr
    e = env(proj)
    assert e["SECRET_KEY"] == first["SECRET_KEY"] and e["POSTGRES_PASSWORD"] == first["POSTGRES_PASSWORD"]
    assert e["TELEGRAM_BOT_TOKEN"] == "111:new_token" and e["TELEGRAM_BOT_USERNAME"] == "OtherBot"
    assert e["OPENAI_API_KEY"] == "new-key"
    assert "secrets kept" in r.stdout


def test_rerun_backs_up_previous_env(proj):
    run(proj, TOKEN, "", "old-key", "203.0.113.7", "")
    before = (proj / ".env").read_bytes()
    run(proj, TOKEN, "", "new-key", "", "")
    (b,) = backups(proj)
    assert (proj / b).read_bytes() == before


def test_rerun_keeps_address_when_ip_unchanged(proj):
    run(proj, TOKEN, "", "k", "203.0.113.7", "reels.example.com")
    assert run(proj, TOKEN, "", "k", "", "").returncode == 0  # accept both defaults
    assert env(proj)["SITE_ADDRESS"] == "reels.example.com"


def test_rerun_with_new_ip_suggests_new_sslip_address(proj):
    run(proj, TOKEN, "", "k", "203.0.113.7", "reels.example.com")
    run(proj, TOKEN, "", "k", "192.0.2.44", "")
    e = env(proj)
    assert e["SITE_IP"] == "192.0.2.44" and e["SITE_ADDRESS"] == "192-0-2-44.sslip.io"
    assert e["CORS_ORIGINS"] == "https://192-0-2-44.sslip.io,https://192.0.2.44"


def test_rerun_generates_missing_secrets_only(proj):
    (proj / ".env").write_text("SECRET_KEY=keep-me\nSITE_IP=203.0.113.7\n")
    run(proj, TOKEN, "", "k", "", "")
    e = env(proj)
    assert e["SECRET_KEY"] == "keep-me" and len(e["POSTGRES_PASSWORD"]) == 32


def test_rerun_uses_last_duplicate_value(proj):
    (proj / ".env").write_text("POSTGRES_PASSWORD=old\nPOSTGRES_PASSWORD=current\n")
    run(proj, TOKEN, "", "k", "203.0.113.7", "")
    assert env(proj)["POSTGRES_PASSWORD"] == "current"


def test_each_key_written_once(proj):
    run(proj, TOKEN, "", "k", "203.0.113.7", "")
    run(proj, TOKEN, "", "k", "", "")
    keys = [l.split("=", 1)[0] for l in (proj / ".env").read_text().splitlines() if "=" in l and not l.startswith("#")]
    assert len(keys) == len(set(keys))


# --------------------------------------------------------------------------- config check
def test_reports_missing_docker(proj):
    r = run(proj, TOKEN, "", "k", "203.0.113.7", "")
    assert "docker is not installed" in r.stdout and "Config check passed" not in r.stdout


def test_reports_passing_config(proj):
    docker(proj, ok=True)
    r = run(proj, TOKEN, "", "k", "203.0.113.7", "")
    assert "Config check passed" in r.stdout and "up -d --build" in r.stdout


def test_reports_this_runs_config_errors_only(proj):
    stale = Path("/tmp/compose-err")
    docker(proj, ok=False, err="service caddy: SITE_IP missing")
    r = run(proj, TOKEN, "", "k", "203.0.113.7", "")
    assert "Config check did not pass" in r.stdout and "SITE_IP missing" in r.stdout
    docker(proj, ok=True)  # a later run never shows the earlier errors
    r = run(proj, TOKEN, "", "k", "", "")
    assert "SITE_IP missing" not in r.stdout
    assert not stale.exists() or "SITE_IP missing" not in stale.read_text()
