"""Dump a table to CSV before a migration touches it.

Run this before every `alembic upgrade head` that alters an existing table.
The output is the proof the change can be reversed, and the baseline for the
before/after diff the backfill protocol asks for.

    uv run python scripts/snapshot_table.py ease_bookings

Writes to `/tmp/ease-drive-snapshots/<table>-<utc timestamp>.csv` and prints
the path. Row counts above SAFETY_LIMIT stop rather than silently writing a
file too large to be useful; pass --force once you have decided that is fine.

Read-only by construction: the only SQL it issues is a COUNT and a SELECT.
"""

import argparse
import asyncio
import csv
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import get_settings
from app.db.naming import is_owned_table
from app.db.session import get_engine

SNAPSHOT_DIR = Path("/tmp/ease-drive-snapshots")
SAFETY_LIMIT = 100_000


async def _snapshot(connection: AsyncConnection, table: str, path: Path) -> int:
    """Stream the table into `path`, returning the row count written."""
    # Identifier cannot be bound as a parameter. `is_owned_table` upstream and
    # the regex below are what keep this from being an injection point.
    result = await connection.stream(text(f'SELECT * FROM "{table}"'))
    written = 0

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(result.keys())
        async for row in result:
            writer.writerow(row)
            written += 1

    return written


async def main(table: str, force: bool) -> int:
    if not table.replace("_", "").isalnum():
        print(f"refusing a table name that is not plain alphanumeric: {table!r}")
        return 2

    if not is_owned_table(table):
        print(f"{table!r} is not one of this application's tables (ease_*).")
        return 2

    settings = get_settings()
    if not settings.is_database_configured:
        print("DATABASE_URL is not set. Nothing to snapshot.")
        return 2

    assert settings.database_url is not None
    engine = get_engine(settings.database_url)

    async with engine.connect() as connection:
        count = await connection.scalar(text(f'SELECT count(*) FROM "{table}"'))
        rows = int(count or 0)
        print(f"{table}: {rows} rows")

        if rows > SAFETY_LIMIT and not force:
            print(
                f"above the {SAFETY_LIMIT} row safety limit. "
                "Re-run with --force once you have decided that is acceptable."
            )
            return 1

        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = SNAPSHOT_DIR / f"{table}-{stamp}.csv"
        written = await _snapshot(connection, table, path)

    await engine.dispose()
    print(f"wrote {written} rows to {path}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("table", help="table to snapshot, e.g. ease_bookings")
    parser.add_argument(
        "--force", action="store_true", help=f"allow more than {SAFETY_LIMIT} rows"
    )
    arguments = parser.parse_args()
    sys.exit(asyncio.run(main(arguments.table, arguments.force)))
