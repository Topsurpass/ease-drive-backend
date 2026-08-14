"""Table naming rules, and the guard that keeps Alembic to our own tables.

Lives here rather than in `alembic/env.py` so it can be imported and tested in
the gate lane. `env.py` executes migrations as a side effect of import, which
makes anything defined inside it effectively untestable.

Why the guard exists: a Neon database may be shared with another application
(nibbs-report owns `nibbs_*`). Autogenerate compares the live database against
`Base.metadata`, so any table it can see that we do not model reads as
"dropped" and earns a `DROP TABLE` in the generated migration. Restricting what
autogenerate can see is what makes that impossible.
"""

from typing import Final

TABLE_PREFIX: Final[str] = "ease_"

# Not the default `alembic_version`: two apps sharing a database would other
# wise overwrite each other's migration state.
VERSION_TABLE: Final[str] = "ease_alembic_version"


def is_owned_table(name: str | None) -> bool:
    """True when a table belongs to this application."""
    return bool(name) and name is not None and name.startswith(TABLE_PREFIX)


def include_name(
    name: str | None, type_: str, _parent_names: dict[str, str | None]
) -> bool:
    """Hide every table that is not ours from autogenerate's comparison."""
    if type_ == "table":
        return is_owned_table(name)
    return True
