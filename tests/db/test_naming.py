"""Gate tests for the Alembic autogenerate guard.

The failure this prevents is the worst one in the repo: autogenerate comparing
`Base.metadata` against a shared database, seeing another application's tables
as missing from the model, and writing `DROP TABLE` for each. Covered here, in
the fast lane, rather than by hoping someone reads every generated migration.
"""

from app.db.base import Base
from app.db.naming import (
    TABLE_PREFIX,
    VERSION_TABLE,
    include_name,
    is_owned_table,
)

NO_PARENTS: dict[str, str | None] = {}


def test_our_tables_are_visible() -> None:
    assert include_name("ease_bookings", "table", NO_PARENTS) is True


def test_another_apps_tables_are_hidden() -> None:
    """nibbs-report owns these. Seeing them is what produces DROP TABLE."""
    for table in ("nibbs_users", "nibbs_sessions", "nibbs_banks"):
        assert include_name(table, "table", NO_PARENTS) is False


def test_unprefixed_tables_are_hidden() -> None:
    """Including a stray `users` table would put it up for deletion too."""
    assert include_name("users", "table", NO_PARENTS) is False


def test_a_missing_name_is_hidden() -> None:
    assert include_name(None, "table", NO_PARENTS) is False


def test_non_table_objects_pass_through() -> None:
    """Indexes and columns are filtered by their table, not by their own name."""
    assert include_name("ix_whatever", "index", NO_PARENTS) is True
    assert include_name("email", "column", NO_PARENTS) is True


def test_every_declared_table_is_prefixed() -> None:
    """A model without the prefix is invisible to autogenerate and never ships."""
    unprefixed = [
        name for name in Base.metadata.tables if not name.startswith(TABLE_PREFIX)
    ]
    assert unprefixed == [], f"these tables would never be migrated: {unprefixed}"


def test_every_declared_table_survives_the_filter() -> None:
    for name in Base.metadata.tables:
        assert include_name(name, "table", NO_PARENTS) is True


def test_version_table_is_scoped_to_this_app() -> None:
    """The default `alembic_version` would collide on a shared database."""
    assert VERSION_TABLE == "ease_alembic_version"
    assert is_owned_table(VERSION_TABLE)
