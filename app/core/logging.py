"""Process-wide logging setup.

A log line nothing is listening to is not a log line. uvicorn configures
handlers for its own `uvicorn*` loggers and leaves the root logger bare, so a
record from this package falls through to logging's `lastResort` handler, which
drops anything below WARNING. That is why the startup line reporting the
resolved CORS allowlist was invisible on a deployed host, which is the only
place it is worth reading.

Installing a root handler does not duplicate uvicorn's output: its loggers set
`propagate = False`.
"""

import logging
from typing import Final

FORMAT: Final[str] = "%(levelname)s %(name)s %(message)s"


def configure_logging(
    level: int = logging.INFO, root: logging.Logger | None = None
) -> None:
    """Install a stderr handler unless something already configured one.

    Deferential on purpose. A platform that has set up its own logging
    (structured JSON, a shipper, pytest's capture) keeps it, and this becomes a
    no-op, because forcing a level onto someone else's configuration is the
    wrong reading of "configure".

    `root` exists so the gate can exercise this against a throwaway logger.
    `logging.basicConfig` would do the same job, but only ever to the real root
    logger, which under pytest is already configured — the test would take the
    early-return branch every time and assert nothing.
    """
    target = root if root is not None else logging.getLogger()
    if target.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(FORMAT))
    target.addHandler(handler)
    target.setLevel(level)
