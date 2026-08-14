#!/usr/bin/env bash
# Gate suite: deterministic, offline, free, sub-2s. Runs on every commit.
set -euo pipefail

cd "$(dirname "$0")/.."

export PATH="$HOME/.local/bin:$PATH"

echo "==> ruff check"
uv run ruff check .

echo "==> ruff format --check"
uv run ruff format --check .

echo "==> mypy (strict)"
uv run mypy

echo "==> pytest"
uv run pytest

echo "==> all gates green"
