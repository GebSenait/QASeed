"""Smoke checks for the Day 2 project skeleton."""

from qaseed import __version__
from qaseed.config import ROOT, SCHEMA_PATH, database_path

REQUIRED_FILES = (
    "README.md",
    "requirements.txt",
    "assets/brand/qaseed_logo.png",
    "app.py",
    ".gitignore",
    ".env.example",
    "data/schema.sql",
    "qaseed/__init__.py",
    "qaseed/config.py",
    "qaseed/schema.py",
    "qaseed/llm_ping.py",
    "qaseed/llm_client.py",
    "qaseed/pipeline.py",
    "qaseed/export.py",
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
    "_project_references/",
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
    assert "from qaseed import __version__, pipeline" in source  # Day 5: UI goes through the service layer
    assert "Scenario generation is not yet implemented." not in source


def test_every_module_has_its_own_test_file():
    """Project rule: each qaseed/<name>.py and app.py has tests/test_<name>.py."""
    modules = [path for path in (ROOT / "qaseed").glob("*.py") if path.name != "__init__.py"]
    modules.append(ROOT / "app.py")
    missing = [f"tests/test_{path.stem}.py" for path in modules if not (ROOT / "tests" / f"test_{path.stem}.py").is_file()]
    assert missing == []


def _local_identifiers() -> list[str]:
    """This machine's account and home-folder names, read at test time (nothing hard-coded)."""
    import getpass
    from pathlib import Path

    names = {getpass.getuser(), Path.home().name}
    return sorted(name for name in names if name and len(name) >= 4)


def test_published_files_do_not_contain_local_user_names_or_paths():
    """Docs, README and reports are pushed to GitHub: no account names or home-folder paths."""
    import re
    from pathlib import Path

    home = str(Path.home())
    targets = [ROOT / "README.md", *(ROOT / "docs").rglob("*"), *(ROOT / "reports").rglob("*")]
    leaks = []
    for path in targets:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        lowered = text.lower()
        if home.lower() in lowered or home.replace("\\", "\\\\").lower() in lowered:
            leaks.append(f"{path.relative_to(ROOT)}: home folder path")
        # Whole words only: the account name can be part of the author's public name, which may appear.
        leaks += [f"{path.relative_to(ROOT)}: account name" for name in _local_identifiers()
                  if re.search(rf"(?<![a-z0-9]){re.escape(name.lower())}(?![a-z0-9])", lowered)]
    assert leaks == []


# Import name -> distribution name in requirements.txt (pytest_metadata is installed by pytest-html).
_DISTRIBUTIONS = {"dotenv": "python-dotenv", "pytest_metadata": "pytest-html"}


def test_one_dependency_file_covers_every_third_party_import():
    """Day 6: requirements.txt is the only dependency file and lists every package the code imports."""
    import ast
    import re
    import sys

    assert not (ROOT / "requirements-dev.txt").exists()
    listed = {
        re.split(r"[<>=!~\[ ;]", line.strip(), maxsplit=1)[0].lower()
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    sources = [ROOT / "app.py", *(ROOT / "qaseed").glob("*.py"), *(ROOT / "scripts").glob("*.py"), *(ROOT / "tests").glob("*.py")]
    local = {"qaseed", "tests", "scripts"} | {path.stem for path in sources}
    imported = set()
    for path in sources:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                imported.add(node.module.split(".")[0])
    third_party = {name for name in imported if name not in sys.stdlib_module_names and name not in local}
    missing = sorted(name for name in third_party if _DISTRIBUTIONS.get(name, name).lower() not in listed)
    assert third_party and missing == []

