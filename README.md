# ease-drive-backend

Services-first Python backend. The root holds glue only — orchestration
scripts, shared config, and tooling. Business logic lives in `services/`.

```
.
├── pyproject.toml     # uv workspace root, tooling config (ruff/mypy/pytest)
├── scripts/check.sh   # the gate suite
├── .githooks/         # pre-commit hook
└── services/
    └── api/           # HTTP API — see services/api/README.md
```

## Setup

Requires [uv](https://docs.astral.sh/uv/). The system Python here is 3.8, which
is too old, so uv manages a pinned 3.13 toolchain instead (`.python-version`).

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # if uv is missing
export PATH="$HOME/.local/bin:$PATH"              # add to ~/.bashrc
uv sync                                           # creates .venv from uv.lock
git config core.hooksPath .githooks               # enable the pre-commit gate
```

## Run

```bash
uv run uvicorn api.main:app --reload
curl localhost:8000/
# {"message":"Hello, World!"}
```

## Check

```bash
./scripts/check.sh
```

Runs ruff (lint + format), mypy in strict mode, and pytest. Deterministic,
offline, under two seconds. The pre-commit hook runs exactly this.

## Typing

`mypy --strict` across sources and tests, plus `warn_unreachable`,
`disallow_any_unimported`, and the `redundant-expr` / `possibly-undefined` /
`truthy-bool` / `ignore-without-code` error codes. Every function is annotated,
including tests. Packages ship `py.typed`.

One deliberate exception is documented in `pyproject.toml`:
`disallow_any_explicit` is off because pydantic's `BaseModel` declares explicit
`Any` in inherited attributes, so it errors on every model definition
regardless of our code.

## Adding a service

New directory under `services/`, its own `pyproject.toml`, `src/`, `tests/`,
and `README.md`. The uv workspace picks it up via the `services/*` glob. Cross
service communication goes through declared contracts, never internals.
