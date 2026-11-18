"""Shared logging and retry helpers."""
from __future__ import annotations

import logging
import random
import time


def get_logger(name: str = None) -> logging.Logger:
    return logging.getLogger(name or "morning-deal-brief")


def retry(fn, attempts: int = 3, base_delay: float = 1.0, backoff: float = 2.0,
          retry_on=Exception, logger: logging.Logger = None):
    """Call ``fn`` with exponential backoff + jitter on transient failures."""
    last_exc = None
    for i in range(max(1, attempts)):
        try:
            return fn()
        except retry_on as exc:
            last_exc = exc
            if i == attempts - 1:
                break
            delay = base_delay * (backoff ** i) + random.uniform(0, 0.5)
            if logger:
                logger.warning("retry %d/%d after error: %s", i + 1, attempts, exc)
            time.sleep(delay)
    raise last_exc
