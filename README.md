# ease-drive-backend

FastAPI backend, laid out the conventional way: one `app/` package split by
technical layer, tests mirroring it. Matches the official docs'
[Bigger Applications](https://fastapi.tiangolo.com/tutorial/bigger-applications/)
guide and `fastapi/full-stack-fastapi-template`.

## Layout

```
app/
├── __init__.py
├── main.py                    # create_app() factory + `app` ASGI entrypoint
├── py.typed
├── core/
│   ├── __init__.py
│   └── config.py              # Settings (pydantic-settings) + get_settings()
├── api/
│   ├── __init__.py
│   ├── deps.py                # shared dependencies as Annotated aliases
│   └── v1/
│       ├── __init__.py
│       ├── router.py          # aggregates every v1 endpoint router
│       └── endpoints/
│           ├── __init__.py
│           └── hello.py       # GET /hello
├── schemas/
│   ├── __init__.py
│   └── hello.py               # HelloResponse
├── models/
│   └── __init__.py            # empty until a database lands
└── services/
    ├── __init__.py
    └── hello.py               # business logic, HTTP-free

tests/                         # mirrors app/ exactly
├── conftest.py
├── test_main.py
├── test_packaging.py           # guards the deploy-time dependency declaration
├── api/v1/test_hello.py
├── core/test_config.py
├── schemas/test_hello.py
└── services/test_hello.py

scripts/check.sh               # the gate suite
.githooks/pre-commit           # runs the gate before every commit
```

**Which layer owns what.** Dependencies point one way:
`endpoints → services → models`. Nothing under `services/` or `models/` imports
`fastapi`, raises `HTTPException`, or knows a status code — endpoints translate
between HTTP and the domain. That is what lets `tests/services/` run without a
client. `app/schemas/` is the wire contract; `app/models/` is the database. They
stay separate so a private column can never leak into a response.

## Endpoint

| Method | Path             | Status | Body                            |
| ------ | ---------------- | ------ | ------------------------------- |
| `GET`  | `/api/v1/hello`  | `200`  | `{"message": "Hello, World!"}`  |

Interactive docs at `/docs`, schema at `/api/v1/openapi.json`.

The `/api/v1` prefix comes from `EASE_DRIVE_API_V1_PREFIX`. Set it to `""` if
you want the route at `/hello` instead.

## Setup

Requires [uv](https://docs.astral.sh/uv/). The system Python here is 3.8, which
is too old for this stack, so uv manages a pinned 3.13 toolchain
(`.python-version`).

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # if uv is missing
export PATH="$HOME/.local/bin:$PATH"              # add to ~/.bashrc
uv sync                                           # builds .venv from uv.lock
source .venv/bin/activate
git config core.hooksPath .githooks               # enable the pre-commit gate
```

## Run

```bash
uvicorn app.main:app --reload
curl localhost:8000/api/v1/hello
# {"message":"Hello, World!"}
```

## Deploy

```bash
fastapi login
fastapi deploy
```

The deploy image installs only what `pyproject.toml` declares, so `fastapi` is
declared **with the `standard` extra**. Plain `fastapi` does not depend on
`fastapi-cli`, and the `fastapi` console script raises at startup without it:

```
RuntimeError: To use the fastapi command, please install "fastapi[standard]"
```

A local venv can carry an orphaned `fastapi-cli` and mask this, which is how it
shipped broken once. `tests/test_packaging.py` asserts the declaration in
`pyproject.toml`, not just that the module imports, because the import check
passes while the deploy is broken.

`[tool.fastapi] entrypoint = "app.main:app"` pins what gets served. Without it
the CLI auto-discovers, which silently picks a different module if files move.

To reproduce the deploy environment locally:

```bash
UV_PROJECT_ENVIRONMENT=/tmp/deploy-venv uv sync --frozen --no-dev
/tmp/deploy-venv/bin/fastapi run
```

## Configuration

Settings live in `app/core/config.py`. Every field reads from an
`EASE_DRIVE_`-prefixed environment variable or a local `.env`, falling back to
the declared default. Copy `.env.example` to `.env` to override locally.

```bash
EASE_DRIVE_GREETING="Welcome to Ease Drive" uvicorn app.main:app
curl localhost:8000/api/v1/hello
# {"message":"Welcome to Ease Drive"}
```

Endpoints read settings through the `SettingsDep` dependency in
`app/api/deps.py`, never `os.environ` directly. That indirection is what lets
tests swap configuration via `app.dependency_overrides`.

## Check

```bash
./scripts/check.sh
```

Runs ruff (lint + format), `mypy --strict`, and pytest. 30 tests, deterministic,
offline, under two seconds. The pre-commit hook runs exactly this.

## Typing

`mypy --strict` across `app/` and `tests/`, plus `warn_unreachable`,
`disallow_any_unimported`, and the `redundant-expr` / `possibly-undefined` /
`truthy-bool` / `ignore-without-code` error codes. Every function is annotated,
including tests. The package ships `py.typed`.

One deliberate exception is documented in `pyproject.toml`:
`disallow_any_explicit` is off because pydantic's `BaseModel` declares explicit
`Any` in inherited attributes, so it errors on every model definition
regardless of our code.

## Adding a resource

Four files plus the test, following `hello` as the template:

1. `app/schemas/<name>.py` — request/response models
2. `app/services/<name>.py` — business logic, no FastAPI imports
3. `app/api/v1/endpoints/<name>.py` — the `APIRouter`
4. `app/api/v1/router.py` — one `include_router` line
5. `tests/api/v1/test_<name>.py` — plus mirrored schema/service tests

Breaking an existing contract means a new `app/api/v2/`, not an edit to v1.

## Evals

None. Evals measure non-deterministic output; every path here is deterministic
and covered by gate tests. Add an eval suite the first time this backend calls
an LLM.
