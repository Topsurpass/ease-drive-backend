"""Gate tests for Neon URL normalization.

This is the layer that decides whether the app can talk to Neon at all, and
every failure mode it handles is one that only shows up against a real server.
"""

from app.db.url import describe_database_url, normalize_database_url

# Shaped exactly like a real Neon URL, with a placeholder host: the tests care
# about the `-pooler` marker and the query parameters, not the endpoint id.
NEON_POOLED = (
    "postgresql://user:secret@ep-example-1234-pooler.c-3.us-east-1"
    ".aws.neon.tech/appdb?channel_binding=require&sslmode=require"
)
NEON_DIRECT = (
    "postgresql://user:secret@ep-example-1234.c-3.us-east-1"
    ".aws.neon.tech/appdb?sslmode=require"
)


def test_names_the_async_driver() -> None:
    """Without the +asyncpg suffix SQLAlchemy loads psycopg2 and fails."""
    url, _ = normalize_database_url(NEON_POOLED)
    assert url.startswith("postgresql+asyncpg://")


def test_strips_libpq_only_parameters() -> None:
    """asyncpg raises TypeError on sslmode / channel_binding as kwargs."""
    url, _ = normalize_database_url(NEON_POOLED)
    assert "sslmode" not in url
    assert "channel_binding" not in url


def test_keeps_credentials_and_database() -> None:
    url, _ = normalize_database_url(NEON_POOLED)
    assert "user:secret@" in url
    assert url.endswith("/appdb")


def test_requests_tls_when_sslmode_requires_it() -> None:
    _, connect_args = normalize_database_url(NEON_POOLED)
    assert connect_args["ssl"] is True


def test_defaults_to_tls_when_sslmode_is_absent() -> None:
    """Neon always requires TLS; a bare URL must not silently downgrade."""
    _, connect_args = normalize_database_url(
        "postgresql://user:secret@ep-x.aws.neon.tech/db"
    )
    assert connect_args["ssl"] is True


def test_disables_statement_cache_on_pooled_hosts() -> None:
    """PgBouncer transaction mode cannot reuse server-side prepared statements."""
    _, connect_args = normalize_database_url(NEON_POOLED)
    assert connect_args["statement_cache_size"] == 0
    assert connect_args["prepared_statement_cache_size"] == 0


def test_keeps_statement_cache_on_direct_hosts() -> None:
    """A direct connection has no pooler, so the cache is a free speedup."""
    _, connect_args = normalize_database_url(NEON_DIRECT)
    assert "statement_cache_size" not in connect_args


def test_is_idempotent() -> None:
    """Normalizing an already-normalized URL must not corrupt it."""
    once, args_once = normalize_database_url(NEON_POOLED)
    twice, args_twice = normalize_database_url(once)
    assert once == twice
    assert args_once == args_twice


def test_preserves_unrelated_query_parameters() -> None:
    url, _ = normalize_database_url(
        "postgresql://u:p@ep-x.aws.neon.tech/db?sslmode=require&application_name=ease"
    )
    assert "application_name=ease" in url


def test_tolerates_surrounding_whitespace() -> None:
    url, _ = normalize_database_url(f"  {NEON_POOLED}\n")
    assert url.startswith("postgresql+asyncpg://")


def test_leaves_a_non_postgres_url_alone() -> None:
    """Those connect_args are asyncpg's; another driver would raise on them."""
    url, connect_args = normalize_database_url("sqlite+aiosqlite:///./local.db")
    assert url == "sqlite+aiosqlite:///./local.db"
    assert connect_args == {}


def test_disables_ssl_when_sslmode_says_so() -> None:
    _, connect_args = normalize_database_url(
        "postgresql://u:p@localhost:5432/db?sslmode=disable"
    )
    assert "ssl" not in connect_args


def test_describe_omits_the_password() -> None:
    """This output goes into /health, which is unauthenticated."""
    described = describe_database_url(NEON_POOLED)
    assert "secret" not in str(described)
    assert described["database"] == "appdb"
    assert described["pooled"] == "True"
