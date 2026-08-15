"""Gate tests for CORS origin parsing.

Every case here is a way a correct-looking configuration silently blocks the
browser, because Starlette matches the `Origin` header by exact string.
"""

import pytest

from app.core.cors import normalize_origin, parse_origins


def test_strips_a_trailing_slash() -> None:
    """A URL copied from an address bar carries one; an Origin header never does."""
    assert (
        normalize_origin("https://ease-drive-backend.fastapicloud.dev/")
        == "https://ease-drive-backend.fastapicloud.dev"
    )


def test_strips_a_path() -> None:
    assert normalize_origin("https://example.com/api/v1") == "https://example.com"


def test_strips_query_and_fragment() -> None:
    assert normalize_origin("https://example.com/?a=1#x") == "https://example.com"


def test_keeps_an_explicit_port() -> None:
    """localhost:3000 and localhost are different origins to a browser."""
    assert normalize_origin("http://localhost:3000/") == "http://localhost:3000"


def test_lower_cases_scheme_and_host() -> None:
    assert normalize_origin("HTTPS://Example.COM") == "https://example.com"


def test_assumes_https_for_a_bare_host() -> None:
    assert normalize_origin("example.com") == "https://example.com"


def test_preserves_http_scheme() -> None:
    """Downgrading a local http origin to https would break dev."""
    assert normalize_origin("http://localhost:3000") == "http://localhost:3000"


def test_passes_the_wildcard_through() -> None:
    assert normalize_origin("*") == "*"


@pytest.mark.parametrize("value", ["", "   ", "https://"])
def test_discards_unusable_values(value: str) -> None:
    assert normalize_origin(value) == ""


def test_parses_a_comma_separated_line() -> None:
    """What a hosting dashboard's single-line env field invites."""
    assert parse_origins("https://a.com, https://b.com") == (
        "https://a.com",
        "https://b.com",
    )


def test_parses_a_json_array() -> None:
    assert parse_origins('["https://a.com","https://b.com"]') == (
        "https://a.com",
        "https://b.com",
    )


def test_parses_a_list() -> None:
    assert parse_origins(["https://a.com/"]) == ("https://a.com",)


def test_drops_blank_entries() -> None:
    """A trailing comma must not become an empty origin that matches nothing."""
    assert parse_origins("https://a.com,,  ,") == ("https://a.com",)


def test_deduplicates_while_keeping_order() -> None:
    assert parse_origins("https://a.com/, https://a.com, https://b.com") == (
        "https://a.com",
        "https://b.com",
    )


# How Settings resolves these origins lives in tests/core/test_config.py; this
# module covers the parsing in isolation.
