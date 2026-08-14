# ease-drive-backend

FastAPI backend, laid out the conventional way: one `app/` package split by
technical layer, tests mirroring it. Matches the official docs'
[Bigger Applications](https://fastapi.tiangolo.com/tutorial/bigger-applications/)
guide and `fastapi/full-stack-fastapi-template`.

## Layout

```
app/
├── main.py                    # create_app() factory + `app` ASGI entrypoint
├── core/
│   ├── config.py              # Settings (pydantic-settings) + get_settings()
│   ├── cors.py                # origin normalization for the CORS allowlist
│   └── errors.py              # unhandled errors → JSON, inside the CORS layer
├── db/
│   ├── url.py                 # Neon URL → asyncpg URL + connect_args
│   ├── naming.py              # ease_ prefix + Alembic autogenerate guard
│   ├── base.py                # DeclarativeBase
│   └── session.py             # engine registry, session_scope, disposal
├── api/
│   ├── deps.py                # SettingsDep, SessionDep
│   └── v1/
│       ├── router.py          # aggregates every v1 endpoint router
│       └── endpoints/
│           ├── hello.py       # GET  /hello
│           ├── health.py      # GET  /health
│           └── booking.py     # POST /bookings
├── schemas/                   # pydantic wire contracts
│   ├── hello.py
│   └── booking.py             # BookingRequest / Accepted / Error
├── models/
│   └── booking.py             # ease_bookings table
└── services/                  # business logic, HTTP-free
    ├── hello.py
    └── booking.py

alembic/
├── env.py                     # async, reads DATABASE_URL via app.core.config
└── versions/                  # migrations

tests/                         # mirrors app/
├── conftest.py                # settings, sqlite engine, client, db_client
├── test_main.py
├── test_packaging.py          # guards the deploy-time dependency declaration
├── api/v1/{test_hello,test_health,test_booking}.py
├── core/{test_config,test_cors,test_errors}.py
├── db/{test_url,test_naming}.py
├── schemas/{test_hello,test_booking}.py
├── services/{test_hello,test_booking}.py
└── integration/test_neon.py   # real Neon; excluded from the gate

scripts/check.sh               # the gate suite
.githooks/pre-commit           # runs the gate before every commit
```

**Which layer owns what.** Dependencies point one way:
`endpoints → services → models`. Nothing under `services/` or `models/` imports
`fastapi`, raises `HTTPException`, or knows a status code — endpoints translate
between HTTP and the domain. That is what lets `tests/services/` run without a
client. `app/schemas/` is the wire contract; `app/models/` is the database. They
stay separate so a private column can never leak into a response.

## Endpoints

| Method | Path               | Status | Body                                          |
| ------ | ------------------ | ------ | --------------------------------------------- |
| `GET`  | `/api/v1/hello`    | `200`  | `{"message": "Hello, World!"}`                 |
| `GET`  | `/api/v1/health`   | `200`  | deployment diagnostic, see below               |
| `POST` | `/api/v1/bookings` | `201`  | `{"ok": true, "reference": "ED-XXXXXX", ...}`  |

Interactive docs at `/docs`, schema at `/api/v1/openapi.json`.

The `/api/v1` prefix comes from `EASE_DRIVE_API_V1_PREFIX`. Set it to `""` if
you want the routes unversioned.

## POST /api/v1/bookings

Persists a booking from the marketing site's booking form and returns a
reference. The request and response shapes are copied from the frontend, which
declares itself the source of truth:

- request: `ease-drive-frontend/src/lib/validators/booking.schema.ts`
- response: `ease-drive-frontend/src/services/booking/index.ts`

```bash
curl -X POST localhost:8000/api/v1/bookings \
  -H 'Content-Type: application/json' \
  -d '{
    "fullName": "Ada Lovelace",
    "phone": "+234 800 000 0000",
    "email": "ada@example.com",
    "tripType": "airport",
    "pickupLocation": "Ikeja GRA",
    "destination": "Murtala Muhammed Airport",
    "startDate": "2026-09-01",
    "durationDays": 2,
    "passengers": 3,
    "notes": "Two large suitcases."
  }'
# {"ok":true,"reference":"ED-VS9T74","receivedAt":"2026-08-14T18:42:45.179524Z"}
```

Fields are camelCase because that is what the zod schema emits; snake_case is
accepted too. `tripType` is one of `interstate`, `intrastate`, `private-driver`,
`family-group`, `airport`, `corporate`, `events`. `durationDays` is 1–30,
`passengers` 1–14, `notes` at most 500 characters and optional.

Failures use the frontend's `BookingFailure` shape rather than FastAPI's default
`{"detail": [...]}`, which the form cannot read:

```jsonc
// 422 — bad input. `detail` is kept for debugging.
{"ok": false, "code": "validation_error", "message": "passengers: Input should be less than or equal to 14", "detail": [...]}

// 503 — DATABASE_URL missing. A deployment fault, not a bad request.
{"ok": false, "code": "unavailable", "message": "The booking service is not configured..."}
```

To wire the frontend up, write its `http-transport.ts` against this endpoint
and call `setBookingTransport(...)`, per
`ease-drive-frontend/src/services/booking/README.md`. No component changes.

## CORS

The browser blocks a cross-origin POST unless this API names the calling origin
in its allowlist. Allowed by default:

| Origin                                        | Why                            |
| --------------------------------------------- | ------------------------------ |
| `https://ease-drive-frontend.vercel.app`       | the deployed frontend          |
| `http://localhost:3000`                        | Next.js dev server             |
| `http://127.0.0.1:3000`                        | same, via loopback IP          |
| `https://ease-drive-backend.fastapicloud.dev`  | this API's own deployed origin |

Override with a comma-separated list (a JSON array also works). Setting it
**replaces** the defaults, so keep localhost if you still develop against it:

```bash
EASE_DRIVE_CORS_ORIGINS=http://localhost:3000,https://your-frontend.vercel.app
```

**The entry must be the origin the browser is on, which is the frontend.** This
backend's own URL is the value `NEXT_PUBLIC_API_BASE_URL` points at, not
anything a browser sends as `Origin`; a page calling its own origin is not
cross-origin and never consults this list. If the booking form is blocked, the
origin to add is whatever serves the form.

Entries are normalized by `app/core/cors.py`: a trailing slash, path, query or
fragment is stripped and the host is lower-cased. Starlette matches `Origin` by
exact string, so `https://example.com/` pasted from an address bar would
otherwise never match and fail preflight with nothing in the logs.

Verified against a running server:

```
OPTIONS /api/v1/bookings, Origin: https://ease-drive-frontend.vercel.app
  → 200, access-control-allow-origin: https://ease-drive-frontend.vercel.app
OPTIONS /api/v1/bookings, Origin: https://evil.example
  → 400, no access-control-allow-origin  (browser blocks)
```

### When a CORS error is not a CORS error

Starlette's `ServerErrorMiddleware` sits *outside* every middleware the app
adds, CORS included, so an unhandled exception used to return a bare
`500 text/plain` with no `Access-Control-Allow-Origin` header at all. The
browser then reports the only thing it can see:

```
Access to XMLHttpRequest ... blocked by CORS policy:
No 'Access-Control-Allow-Origin' header is present
```

which blames CORS for a server crash. `app/core/errors.py` catches unhandled
exceptions *inside* the CORS layer, logs the traceback, and returns

```json
{"ok": false, "code": "transport_error", "message": "The server hit an unexpected error. Please try again."}
```

so the response keeps its CORS headers and the frontend can read the failure.
**If you see a CORS error with a 500 next to it, check the server log first —
the allowlist is probably fine.**

## Database

Neon Postgres, reached with SQLAlchemy 2.0 async over asyncpg, migrated with
Alembic.

```bash
cp .env.example .env          # then paste your Neon DATABASE_URL
alembic upgrade head          # create/refresh ease_bookings
curl localhost:8000/api/v1/health
```

`/api/v1/health` reports whether the process can actually reach its database,
including host, database name and latency, and never the password. It answers
`200` even when degraded, because a diagnostic that fails tells you nothing:

```json
{"status":"ok","version":"0.1.0","database":{"configured":true,
 "host":"ep-...-pooler.c-3.us-east-1.aws.neon.tech","database":"nibbsreport",
 "pooled":"True","ok":true,"latency_ms":7967.9,"error":null}}
```

**Tables are prefixed `ease_`.** This Neon database is shared with the
nibbs-report app, whose tables use `nibbs_`. Two consequences worth knowing:

- Alembic's `env.py` filters autogenerate to `ease_` tables, via
  `app/db/naming.py`. Without that filter, autogenerate reads another app's
  tables as "in the database but not in the model" and emits `DROP TABLE` for
  each one. The guard lives in an importable module so `tests/db/test_naming.py`
  covers it in the gate, rather than relying on someone reading every
  generated migration.
- The migration version table is `ease_alembic_version`, not the default, so
  two apps on this database can never fight over migration state.

`app/db/url.py` rewrites the Neon connection string before asyncpg sees it:
the scheme gains `+asyncpg`, the libpq-only `sslmode` and `channel_binding`
parameters are stripped (asyncpg raises `TypeError` on them) and re-expressed
as its `ssl` argument, and a `-pooler` host disables the prepared-statement
caches that PgBouncer's transaction mode cannot reuse.

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

`DATABASE_URL` is the one exception: it is read **unprefixed**, because Neon,
Vercel, Render and Railway all inject that exact name, and requiring
`EASE_DRIVE_DATABASE_URL` would mean hand-copying the credential on every host.
The prefixed form still works as a fallback.

```bash
EASE_DRIVE_GREETING="Welcome to Ease Drive" uvicorn app.main:app
curl localhost:8000/api/v1/hello
# {"message":"Welcome to Ease Drive"}
```

Endpoints read settings through the `SettingsDep` dependency in
`app/api/deps.py`, never `os.environ` directly. That indirection is what lets
tests swap configuration via `app.dependency_overrides`.

## Check

Two lanes.

```bash
./scripts/check.sh          # gate: ruff, mypy --strict, pytest
pytest -m integration       # the Neon lane, run before shipping schema changes
```

The gate is 136 tests, deterministic, offline and free. Database-backed tests
run the real models and the real session machinery against in-memory SQLite, so
no test in this lane touches the network.

The integration lane (`tests/integration/`) is excluded from the gate by
`-m "not integration"` because it is neither offline nor free. It skips rather
than fails when `DATABASE_URL` is absent, and every row it writes is deleted in
the same test.

Wall clock for the gate is about 11s on this machine, of which roughly 9s is
fixed import cost — `import app.main` alone is 4.8s, mostly `fastapi` (2.1s)
and `sqlalchemy` (0.8s). Test execution itself is about 2s. The pre-commit hook
runs the gate lane only.

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

Follow `booking` as the template:

1. `app/schemas/<name>.py` — request/response models
2. `app/models/<name>.py` — the table, plus an import in `app/models/__init__.py`
   so `Base.metadata` is complete when Alembic autogenerates
3. `app/services/<name>.py` — business logic, no FastAPI imports
4. `app/api/v1/endpoints/<name>.py` — the `APIRouter`
5. `app/api/v1/router.py` — one `include_router` line
6. `alembic revision --autogenerate -m "..."`, then **read the migration** before
   applying it
7. `tests/` — mirrored schema, service and endpoint tests

Name every table `ease_*`. A table without that prefix is invisible to Alembic's
autogenerate filter, so it will never be migrated.

Breaking an existing contract means a new `app/api/v2/`, not an edit to v1.

## Evals

None. Evals measure non-deterministic output; every path here is deterministic
and covered by gate tests. Add an eval suite the first time this backend calls
an LLM.
