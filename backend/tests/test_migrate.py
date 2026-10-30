"""Portable migrations preserve data, skip completed revisions, and gate startup."""
import subprocess

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text

from app import migrate, render_runtime
from app.config import get_settings


@pytest.fixture
def migration_db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'migrations.db'}"
    monkeypatch.setattr(get_settings(), "database_url", url)
    # Keep pytest's logging intact when Alembic loads its CLI configuration.
    monkeypatch.setattr("logging.config.fileConfig", lambda *args, **kwargs: None)
    engine = create_engine(url)
    yield engine
    engine.dispose()


def test_fresh_database_reaches_head_and_repeat_run_preserves_data(migration_db, monkeypatch, tmp_path):
    # App importability is independent of the directory containing the config.
    monkeypatch.chdir(tmp_path)
    migrate.run()
    with migration_db.begin() as conn:
        conn.execute(text("""INSERT INTO users
            (id,name,is_anonymous,role,status,credits_balance,created_at,updated_at)
            VALUES ('u','Keep me',0,'user','active',7,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""))
    migrate.run()
    migrate.run(check=True)
    with migration_db.connect() as conn:
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "0004"
        assert conn.scalar(text("SELECT credits_balance FROM users WHERE id='u'")) == 7
    assert "diagnostic" in {c["name"] for c in inspect(migration_db).get_columns("usage_records")}


def test_old_database_applies_0003_and_0004_preserving_usage(migration_db):
    command.upgrade(migrate.migration_config(), "0002")
    with migration_db.begin() as conn:
        conn.execute(text("""INSERT INTO users
            (id,name,is_anonymous,role,status,credits_balance,created_at,updated_at)
            VALUES ('u','Owner',0,'user','active',0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""))
        conn.execute(text("""INSERT INTO usage_records
            (id,user_id,kind,credits_delta,quantity,unit,estimated_cost_usd,created_at)
            VALUES ('usage','u','llm',0,123,'tokens',0,CURRENT_TIMESTAMP)"""))
    with pytest.raises(RuntimeError, match="schema is behind"):
        migrate.run(check=True)
    assert "diagnostic" not in {c["name"] for c in inspect(migration_db).get_columns("usage_records")}
    migrate.run()
    with migration_db.connect() as conn:
        row = conn.execute(text("SELECT quantity,project_id,diagnostic FROM usage_records WHERE id='usage'")).one()
        assert row.quantity == 123 and row.project_id is None and row.diagnostic is None
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "0004"


def test_check_on_empty_database_does_not_create_tables(migration_db):
    with pytest.raises(RuntimeError, match="schema is behind"):
        migrate.run(check=True)
    assert inspect(migration_db).get_table_names() == []


def test_failed_migration_stops_render_before_children_start(monkeypatch, tmp_path):
    monkeypatch.setattr(render_runtime, "prepare_disk", lambda: tmp_path)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused:unused@localhost/unused")
    monkeypatch.setattr(get_settings(), "app_env", "production")
    monkeypatch.setattr(get_settings(), "secret_key", "production-secret-for-startup-test")
    commands = []
    def fail(command, **kwargs):
        commands.append(command)
        raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(render_runtime.subprocess, "run", fail)
    monkeypatch.setattr(render_runtime.os, "execvp", lambda *args: pytest.fail("Services started after migration failure"))
    with pytest.raises(subprocess.CalledProcessError):
        render_runtime.main()
    assert len(commands) == 1 and commands[0][-2:] == ["-m", "app.migrate"]
