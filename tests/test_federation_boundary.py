"""Keeps scrobbler.federation a separate, replaceable module.

Rules (see src/scrobbler/federation/README.md):
  1. Nothing outside scrobbler.federation imports it, except create_app() in
     scrobbler/__init__.py.
  2. scrobbler.federation.protocol is pure: no Flask, SQLAlchemy, Werkzeug or scrobbler
     imports (other than protocol itself).
  3. The rest of scrobbler.federation uses only the core's public surface.
  4. With federation off, nothing of it is registered at runtime.
"""

import ast
from pathlib import Path

import pytest

from scrobbler import create_app, events, worker
from scrobbler.config import TestConfig
from scrobbler.federation.config import FederationConfigError

SRC = Path(__file__).resolve().parents[1] / "src"
PACKAGE = SRC / "scrobbler"

FEDERATION = "scrobbler.federation"
PROTOCOL = "scrobbler.federation.protocol"
FRAMEWORKS = (
    "flask",
    "flask_sqlalchemy",
    "flask_migrate",
    "flask_smorest",
    "flask_cors",
    "sqlalchemy",
    "werkzeug",
    "alembic",
    "marshmallow",
)
CORE_PUBLIC_SURFACE = (
    "scrobbler.events",
    "scrobbler.extensions",
    "scrobbler.worker",
    "scrobbler.services",
    "scrobbler.api.decorators",
    "scrobbler.schemas",
)


def module_name(path: Path) -> str:
    parts = list(path.relative_to(SRC).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def imported_names(module: str, source: str, is_package: bool) -> set[str]:
    """Every module a piece of source imports, anywhere in the file. For
    `from a import b`, both `a` and `a.b` are included (b may be a submodule)."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:  # relative import
                package = module if is_package else module.rpartition(".")[0]
                for _ in range(node.level - 1):
                    package = package.rpartition(".")[0]
                base = f"{package}.{base}" if base else package
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)
    return names


def within(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def violations(module: str, imports: set[str]) -> list[str]:
    problems = []
    if within(module, PROTOCOL):
        for name in imports:
            if any(within(name, fw) for fw in FRAMEWORKS):
                problems.append(f"{module} (protocol) imports framework module {name}")
            elif within(name, "scrobbler") and not within(name, PROTOCOL):
                problems.append(f"{module} (protocol) imports {name}")
    elif within(module, FEDERATION):
        for name in imports:
            if not within(name, "scrobbler") or name == "scrobbler":
                continue
            if within(name, FEDERATION) or any(within(name, ok) for ok in CORE_PUBLIC_SURFACE):
                continue
            problems.append(f"{module} imports core internals: {name}")
    elif module != "scrobbler":  # create_app() may call federation.init_app
        problems += [f"{module} imports {name}" for name in imports if within(name, FEDERATION)]
    return problems


def all_modules():
    for path in sorted(PACKAGE.rglob("*.py")):
        if "migrations" in path.parts:
            continue
        yield module_name(path), path


def test_import_boundaries():
    problems = []
    for module, path in all_modules():
        imports = imported_names(module, path.read_text(), path.name == "__init__.py")
        problems += violations(module, imports)
    assert not problems, "Federation module boundary broken:\n  " + "\n  ".join(problems)


def test_federation_package_is_checked():
    modules = {module for module, _ in all_modules()}
    assert {FEDERATION, PROTOCOL, f"{FEDERATION}.config"} <= modules


@pytest.mark.parametrize(
    ("module", "source", "expected"),
    [
        ("scrobbler.services.stats", "from scrobbler.federation import web", "imports"),
        ("scrobbler.api.apps", "def f():\n    import scrobbler.federation.keys", "imports"),
        (PROTOCOL + ".vocab", "import flask", "framework"),
        (PROTOCOL + ".vocab", "from sqlalchemy import select", "framework"),
        (PROTOCOL + ".vocab", "from scrobbler.models import User", "imports"),
        (PROTOCOL + ".vocab", "from ..config import FederationConfig", "imports"),
        (FEDERATION + ".inbox", "from scrobbler.models import User", "core internals"),
        (FEDERATION + ".inbox", "from scrobbler import metrics", "core internals"),
    ],
)
def test_the_checker_catches_violations(module, source, expected):
    """Proves the rules above can fail, so a passing run means something."""
    found = violations(module, imported_names(module, source, is_package=False))
    assert found and expected in found[0]


@pytest.mark.parametrize(
    ("module", "source"),
    [
        ("scrobbler", "from scrobbler import federation"),
        (
            FEDERATION + ".inbox",
            "from scrobbler.services import stats\nfrom scrobbler import events",
        ),
        (FEDERATION + ".inbox", "from .protocol import vocab\nfrom flask import Blueprint"),
        (PROTOCOL + ".signatures.rfc9421", "from ..vocab import note\nimport hashlib"),
    ],
)
def test_the_checker_allows_permitted_imports(module, source):
    assert violations(module, imported_names(module, source, is_package=False)) == []


# --- Runtime: off means off -----------------------------------------------------------


def _from_federation(obj) -> bool:
    return getattr(obj, "__module__", "").startswith(FEDERATION)


def test_nothing_is_registered_when_federation_is_off(app):
    assert app.extensions["federation"].enabled is False
    views = [view for view in app.view_functions.values() if _from_federation(view)]
    assert views == []
    for signal in (events.scrobbles_stored, events.now_playing_changed, events.import_finished):
        assert not any(_from_federation(r) for r in signal.receivers_for(None))
    assert not any(name.startswith("federation") for name in worker.registered())


class _Enabled(TestConfig):
    FEDERATION_ENABLED = "1"
    FEDERATION_DOMAIN = "scrobble.example"
    FEDERATION_BASE_URL = "https://api.scrobble.example"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"FEDERATION_DOMAIN": ""}, "FEDERATION_DOMAIN"),
        ({"FEDERATION_DOMAIN": "https://scrobble.example"}, "FEDERATION_DOMAIN"),
        ({"FEDERATION_BASE_URL": "http://api.scrobble.example"}, "FEDERATION_BASE_URL"),
        ({"FEDERATION_BASE_URL": ""}, "FEDERATION_BASE_URL"),
    ],
)
def test_enabling_federation_requires_valid_settings(overrides, message):
    config = type("Misconfigured", (_Enabled,), overrides)
    with pytest.raises(FederationConfigError, match=message):
        create_app(config)


def test_federation_can_be_enabled():
    enabled = create_app(_Enabled)
    config = enabled.extensions["federation"]
    assert (config.enabled, config.domain, config.base_url) == (
        True,
        "scrobble.example",
        "https://api.scrobble.example",
    )
