"""Portable schema deployment: python -m app.migrate [--check]."""
from __future__ import annotations

import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from app.config import get_settings


# The same key across deploys serializes PostgreSQL migrators sharing a database.
MIGRATION_LOCK = 72633541802301


def migration_config() -> Config:
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic").replace("%", "%%"))
    return config


def run(*, check: bool = False) -> None:
    config = migration_config()
    expected = set(ScriptDirectory.from_config(config).get_heads())
    if len(expected) != 1:
        raise RuntimeError("Expected one migration head; resolve migration branches before deployment")
    engine = create_engine(get_settings().database_url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            if not check:
                if connection.dialect.name == "postgresql":
                    # Transaction-scoped: released on success, failure, or lost connection.
                    connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK})
                print("Applying pending database migrations…", flush=True)
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
            actual = set(MigrationContext.configure(connection).get_current_heads())
            if actual != expected:
                raise RuntimeError(f"Database schema is behind: current={sorted(actual)}, expected={sorted(expected)}. "
                                   "Run python -m app.migrate before starting services.")
        print(f"Database schema is current ({', '.join(sorted(expected))}).", flush=True)
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify the schema without applying migrations")
    args = parser.parse_args()
    run(check=args.check)


if __name__ == "__main__":
    main()
