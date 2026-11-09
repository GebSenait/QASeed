"""Smoke checks for the Day 2 project skeleton."""

from qaseed import __version__
from qaseed.config import ROOT, SCHEMA_PATH, database_path

REQUIRED_FILES = (
    "README.md",
    "requirements.txt",
    "app.py",
    ".gitignore",
    ".env.example",
    "data/schema.sql",
    "qaseed/__init__.py",
    "qaseed/config.py",
    "qaseed/schema.py",
    "qaseed/llm_ping.py",
    "qaseed/llm_client.py",
)

GITIGNORE_RULES = (
    ".venv/",
    "__pycache__/",
    "*.pyc",
    ".pytest_cache/",
    ".env",
    ".env.*",
    "!.env.example",
    ".streamlit/secrets.toml",
    "*.db",
    "*.sqlite3",
)


def test_package_version():
    assert __version__ == "0.1.0"


def test_required_files_exist():
    missing = [rel for rel in REQUIRED_FILES if not (ROOT / rel).is_file()]
    assert missing == []


def test_schema_file_is_present():
    assert SCHEMA_PATH.is_file()
    text = SCHEMA_PATH.read_text(encoding="utf-8")
    assert "customers" in text
    assert "payments" in text


def test_default_database_path_is_local_and_not_created():
    path = database_path(env={})
    assert path.name == "qaseed.db"
    assert path.is_absolute()
    assert not path.exists()


def test_env_example_has_no_secret_values():
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("QASEED_"):
            continue
        raise AssertionError(f"Unexpected env assignment: {stripped}")


def test_gitignore_covers_secrets_and_databases():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for rule in GITIGNORE_RULES:
        assert rule in text.splitlines()


def test_app_source_compiles():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    compile(source, "app.py", "exec")
    assert "streamlit" in source
    assert "Scenario generation is not yet implemented." in source


def test_every_module_has_its_own_test_file():
    """Project rule: each qaseed/<name>.py and app.py has tests/test_<name>.py."""
    modules = [path for path in (ROOT / "qaseed").glob("*.py") if path.name != "__init__.py"]
    modules.append(ROOT / "app.py")
    missing = [f"tests/test_{path.stem}.py" for path in modules if not (ROOT / "tests" / f"test_{path.stem}.py").is_file()]
    assert missing == []
