"""Declarative base shared by every ORM model.

Kept in its own module so Alembic can import the metadata without pulling in
the engine, which would need a live DATABASE_URL just to autogenerate.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all tables in this project."""
