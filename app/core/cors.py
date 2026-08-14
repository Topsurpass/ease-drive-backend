"""Origin parsing for the CORS allowlist.

A browser's `Origin` header is scheme + host + optional port, and nothing else:
`https://example.com`, never `https://example.com/`. Starlette compares it to
the allowlist by exact string match, so one trailing slash in configuration
means the origin silently never matches and every request fails preflight with
no error anywhere in the logs.

Copy-pasting a URL from a browser address bar is the normal way that slash
arrives, so entries are normalized here rather than trusted.
"""

import json
from typing import Final
from urllib.parse import urlsplit

WILDCARD: Final[str] = "*"


def normalize_origin(value: str) -> str:
    """Reduce a URL to the exact form a browser sends in `Origin`.

    Drops any path, query or fragment, lower-cases the scheme and host (the
    header is always lower-case) and keeps a non-default port.
    """
    candidate = value.strip()
    if not candidate or candidate == WILDCARD:
        return candidate

    # A bare host has no scheme, and urlsplit would read it as a path.
    if "//" not in candidate:
        candidate = f"https://{candidate}"

    split = urlsplit(candidate)
    scheme = split.scheme.lower()
    host = (split.hostname or "").lower()
    if not host:
        return ""

    origin = f"{scheme}://{host}"
    if split.port is not None:
        origin = f"{origin}:{split.port}"
    return origin


def _from_json_array(text: str) -> list[str]:
    """Read a JSON array, falling back to comma splitting on malformed input."""
    try:
        parsed = json.loads(text)
    except ValueError:
        return text.split(",")
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def parse_origins(value: object) -> tuple[str, ...]:
    """Build an allowlist from a comma-separated string, a JSON array or a list.

    Comma-separated is supported because that is what a hosting dashboard's
    single-line env field invites, and requiring JSON there is a reliable way
    to end up with a literal `["https://..."]` as one origin.
    """
    parts: list[str]
    if isinstance(value, str):
        text = value.strip()
        parts = _from_json_array(text) if text.startswith("[") else text.split(",")
    elif isinstance(value, (list, tuple)):
        parts = [str(item) for item in value]
    else:
        parts = [str(value)]

    seen: dict[str, None] = {}
    for part in parts:
        origin = normalize_origin(part)
        if origin:
            seen.setdefault(origin, None)
    return tuple(seen)
