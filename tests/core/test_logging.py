"""Gate tests for logging setup.

The startup report on CORS and DATABASE_URL is the only warning a
misconfigured deploy gets, and uvicorn leaves the root logger bare, so this is
what decides whether that warning is emitted at all.

Every case runs against a throwaway logger rather than the real root, which
pytest has already configured.
"""

import logging

from app.core.logging import configure_logging


def _probe(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.handlers = []
    logger.setLevel(logging.NOTSET)
    return logger


def test_installs_a_handler_when_there_is_none() -> None:
    """Without one, a record falls through to lastResort, which drops INFO."""
    logger = _probe("probe.installs")
    configure_logging(root=logger)
    assert len(logger.handlers) == 1


def test_lets_info_records_through() -> None:
    """WARNING is logging's default; the allowlist line is INFO."""
    logger = _probe("probe.level")
    configure_logging(root=logger)
    assert logger.isEnabledFor(logging.INFO)


def test_respects_an_explicit_level() -> None:
    logger = _probe("probe.explicit")
    configure_logging(level=logging.WARNING, root=logger)
    assert not logger.isEnabledFor(logging.INFO)


def test_is_idempotent() -> None:
    """create_app runs per app; a handler per call would duplicate every line."""
    logger = _probe("probe.idempotent")
    configure_logging(root=logger)
    configure_logging(root=logger)
    assert len(logger.handlers) == 1


def test_leaves_an_existing_configuration_alone() -> None:
    """A host with structured logging or a shipper must keep it."""
    logger = _probe("probe.existing")
    existing = logging.NullHandler()
    logger.addHandler(existing)
    logger.setLevel(logging.ERROR)

    configure_logging(root=logger)

    assert logger.handlers == [existing]
    assert logger.level == logging.ERROR
