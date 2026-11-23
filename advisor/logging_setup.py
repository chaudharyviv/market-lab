"""Logging setup: console plus a rotating file. Safe to call more than once."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from advisor import config

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_MARK = "_advisor_handler"


def setup_logging(level: str | None = None, to_file: bool = True) -> logging.Logger:
    """Configure the 'advisor' logger and return it. Repeat calls replace handlers instead of stacking them."""
    log = logging.getLogger("advisor")
    log.setLevel((level or config.LOG_LEVEL).upper())
    for h in [h for h in log.handlers if getattr(h, _MARK, False)]:
        log.removeHandler(h)
        h.close()
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if to_file:
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(config.LOG_DIR / "advisor.log", maxBytes=2_000_000, backupCount=3,
                                            encoding="utf-8"))
    for h in handlers:
        h.setFormatter(logging.Formatter(_FORMAT))
        setattr(h, _MARK, True)
        log.addHandler(h)
    log.propagate = False
    return log
