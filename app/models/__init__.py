"""Persistence models.

SQLAlchemy table classes only, never Pydantic API schemas, which live in
``app.schemas``. Every model must be imported here so ``Base.metadata`` is
complete when Alembic autogenerates a migration.
"""

from app.models.booking import Booking

__all__ = ["Booking"]
