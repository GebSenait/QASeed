"""Local paths and .env loading (qaseed/config.py)."""

from pathlib import Path

import qaseed.config as config
from qaseed.config import DEFAULT_DB_PATH, ROOT, SCHEMA_PATH, database_path


def test_root_and_schema_path():
    assert (ROOT / "qaseed" / "config.py").is_file()
    assert SCHEMA_PATH == ROOT / "data" / "schema.sql"


def test_relative_db_path_is_resolved_under_the_project():
    path = database_path(env={"QASEED_DB_PATH": "data/custom.db"})
    assert path == ROOT / "data" / "custom.db"


def test_absolute_db_path_is_kept(tmp_path):
    target = tmp_path / "other.db"
    assert database_path(env={"QASEED_DB_PATH": str(target)}) == target


def test_default_db_path():
    assert database_path(env={}) == ROOT / Path(DEFAULT_DB_PATH)


def test_explicit_env_does_not_load_dotenv(monkeypatch):
    def explode():
        raise AssertionError(".env should not be loaded when env is passed")

    monkeypatch.setattr(config, "load_local_env", explode)
    database_path(env={})


def test_load_local_env_reads_only_the_project_env(monkeypatch):
    seen = []
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda path: seen.append(Path(path)))
    config.load_local_env()
    assert seen == [ROOT / ".env"]
