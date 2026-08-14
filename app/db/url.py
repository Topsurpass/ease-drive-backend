"""Turn a Neon connection string into something asyncpg accepts.

Neon hands out a libpq URL:

    postgresql://user:pw@ep-x-pooler.region.aws.neon.tech/db
        ?sslmode=require&channel_binding=require

Three things have to change before SQLAlchemy's asyncpg dialect can use it:

1. The scheme must name the driver (`postgresql+asyncpg`), or SQLAlchemy loads
   psycopg2 and fails on a sync driver inside an async engine.
2. `sslmode` and `channel_binding` are libpq parameters. asyncpg does not accept
   them and raises `TypeError: connect() got an unexpected keyword argument`.
   They are stripped here and re-expressed as asyncpg's own `ssl` argument.
3. A `-pooler` host is Neon's PgBouncer endpoint running in transaction mode,
   where server-side prepared statements are not safe to reuse across
   checkouts. asyncpg caches them by default, which surfaces later as
   `prepared statement "__asyncpg_stmt_x__" does not exist` under load. Both
   caches are disabled for pooled hosts only, so a direct connection keeps them.
"""

from typing import Any, Final
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ASYNC_SCHEME: Final[str] = "postgresql+asyncpg"

# libpq-only query parameters. asyncpg rejects each one as an unknown kwarg.
LIBPQ_ONLY_PARAMS: Final[frozenset[str]] = frozenset(
    {"sslmode", "channel_binding", "sslrootcert", "sslcert", "sslkey", "options"}
)

# sslmode values that mean "encrypt the connection".
SSL_REQUIRED_MODES: Final[frozenset[str]] = frozenset(
    {"require", "verify-ca", "verify-full", "prefer", "allow"}
)

POOLER_HOST_MARKER: Final[str] = "-pooler"


def normalize_database_url(raw: str) -> tuple[str, dict[str, Any]]:
    """Return an asyncpg-ready URL and the connect_args it needs.

    Idempotent: passing an already-normalized URL returns it unchanged.
    """
    text = raw.strip()
    split = urlsplit(text)

    # Everything below is Postgres/asyncpg specific: the connect_args would
    # raise on another driver, and rebuilding a URL whose netloc is empty (as
    # in `sqlite+aiosqlite:///./local.db`) silently drops slashes and changes
    # the path. Anything not Postgres is returned exactly as given.
    if not split.scheme.startswith("postgres"):
        return text, {}

    params = parse_qsl(split.query, keep_blank_values=True)
    kept = [(key, value) for key, value in params if key not in LIBPQ_ONLY_PARAMS]
    dropped = {key: value for key, value in params if key in LIBPQ_ONLY_PARAMS}

    connect_args: dict[str, Any] = {}

    sslmode = dropped.get("sslmode")
    if sslmode is None or sslmode in SSL_REQUIRED_MODES:
        # Neon always requires TLS; default to it rather than silently
        # downgrading a URL that arrived without the parameter.
        connect_args["ssl"] = True

    host = split.hostname or ""
    if POOLER_HOST_MARKER in host:
        connect_args["statement_cache_size"] = 0
        connect_args["prepared_statement_cache_size"] = 0

    normalized = urlunsplit(
        (ASYNC_SCHEME, split.netloc, split.path, urlencode(kept), split.fragment)
    )
    return normalized, connect_args


def describe_database_url(raw: str) -> dict[str, str | None]:
    """Host and database name for diagnostics. Never includes the password."""
    split = urlsplit(raw.strip())
    return {
        "host": split.hostname,
        "database": split.path.lstrip("/") or None,
        "pooled": str(POOLER_HOST_MARKER in (split.hostname or "")),
    }
