# services/api

HTTP API for Ease Drive. Currently exposes one route.

## Contract

| Method | Path | Status | Body |
| ------ | ---- | ------ | ---- |
| `GET`  | `/`  | `200`  | `{"message": "Hello, World!"}` |

The response body is the `HelloResponse` model in `src/api/schemas.py`. It is
frozen and rejects extra fields, so the wire shape is pinned in one place
rather than scattered through handlers. Anything that crosses the HTTP boundary
gets declared there.

## Layout

```
src/api/
├── main.py          # create_app() factory + `app` ASGI entrypoint
├── schemas.py       # response contracts
├── routes/
│   └── hello.py     # the GET / router
└── py.typed         # ships type information to consumers
tests/
├── conftest.py      # `client` fixture, one app per test
├── test_hello.py    # route behaviour over HTTP
├── test_schemas.py  # contract validation rules
└── test_app.py      # factory isolation + OpenAPI surface
```

`create_app()` is a factory rather than a module-level singleton so each test
builds an isolated app with no import-order side effects. `main.app` exists for
uvicorn to import.

## Run

From the repo root:

```bash
uv run uvicorn api.main:app --reload
```

Then `curl localhost:8000/`. Interactive docs at `/docs`, schema at
`/openapi.json`.

## Test

```bash
uv run pytest                    # this service's gate tests
../../scripts/check.sh           # full gate: ruff + mypy + pytest
```

## Evals

None. Evals measure non-deterministic output; every path in this service is
deterministic and fully covered by gate tests. Add an eval suite here the first
time this service calls an LLM.
