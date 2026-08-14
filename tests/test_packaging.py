"""Gate tests for what the deploy image installs.

Regression coverage for a deploy failure on 2026-08-14: `fastapi deploy` died
with `RuntimeError: To use the fastapi command, please install
"fastapi[standard]"`. The cause was a declaration gap, not a missing install.
`pyproject.toml` asked for plain `fastapi`, which does not depend on
`fastapi-cli`; the local venv happened to carry an orphaned copy, so it worked
here and failed in the built image.

These tests assert the declaration rather than the import, because asserting
importability is exactly what would have passed while the deploy was broken.
"""

import importlib
import tomllib
from pathlib import Path
from typing import Final

from fastapi import FastAPI

PYPROJECT: Final[Path] = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _pyproject() -> dict[str, object]:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)


def _dependencies() -> list[str]:
    project = _pyproject()["project"]
    assert isinstance(project, dict)
    deps = project["dependencies"]
    assert isinstance(deps, list)
    return [str(dep) for dep in deps]


def test_declares_the_fastapi_standard_extra() -> None:
    """Plain `fastapi` omits fastapi-cli, so `fastapi run` cannot start."""
    fastapi_deps = [d for d in _dependencies() if d.startswith("fastapi")]
    assert fastapi_deps, "fastapi is not declared at all"
    assert any("[standard]" in d for d in fastapi_deps), (
        f"expected fastapi[standard], found {fastapi_deps}"
    )


def test_fastapi_cli_is_importable() -> None:
    """The `fastapi` console script fails at startup without this module."""
    assert importlib.import_module("fastapi_cli") is not None


def test_declares_the_deploy_entrypoint() -> None:
    """`fastapi run` reads [tool.fastapi].entrypoint; see fastapi_cli/config.py."""
    tool = _pyproject()["tool"]
    assert isinstance(tool, dict)
    fastapi_config = tool["fastapi"]
    assert isinstance(fastapi_config, dict)
    assert fastapi_config["entrypoint"] == "app.main:app"


def test_entrypoint_resolves_to_a_fastapi_instance() -> None:
    """Catches a rename of app/main.py that would break deploys but not tests."""
    tool = _pyproject()["tool"]
    assert isinstance(tool, dict)
    fastapi_config = tool["fastapi"]
    assert isinstance(fastapi_config, dict)
    module_path, _, attribute = str(fastapi_config["entrypoint"]).partition(":")
    resolved = getattr(importlib.import_module(module_path), attribute)
    assert isinstance(resolved, FastAPI)
