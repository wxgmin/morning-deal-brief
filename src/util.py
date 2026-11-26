"""Bounded, status-aware retries for upstream reads."""
from __future__ import annotations

import random
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import requests

# Free endpoints drop connections when ~30 requests arrive at once (seen on Yahoo, Nasdaq, Fed, Treasury).
# Every outbound read takes one global slot and one per-host slot, however many thread pools are nested.
_GLOBAL_SLOTS = threading.BoundedSemaphore(10)
_HOST_SLOTS = defaultdict(lambda: threading.BoundedSemaphore(4))
_HOST_LOCK = threading.Lock()


@contextmanager
def connection_slot(url: str):
    host = (urlsplit(url).hostname or "").lower()
    with _HOST_LOCK:
        host_slot = _HOST_SLOTS[host]
    with _GLOBAL_SLOTS, host_slot:
        yield


def get_bytes(url, headers=None, *, params=None, limit=4_000_000, attempts=2, before_request=None):
    for attempt in range(attempts):
        delay = None
        try:
            if before_request:
                before_request()
            with connection_slot(url), requests.get(url, headers=headers, params=params, timeout=(4, 8),
                                                    stream=True, allow_redirects=False) as response:
                if response.status_code == 200:
                    body = bytearray()
                    deadline = time.monotonic() + 12
                    for chunk in response.iter_content(65536):
                        body.extend(chunk)
                        if len(body) > limit or time.monotonic() > deadline:
                            raise ValueError("upstream response exceeded download limits")
                    return bytes(body)
                if response.status_code not in {408, 425, 429, 500, 502, 503, 504}:
                    raise ValueError(f"upstream rejected request (HTTP {response.status_code})")
                delay = response.headers.get("Retry-After")
                failure = f"HTTP {response.status_code}"
        except (requests.Timeout, requests.ConnectionError, requests.exceptions.ChunkedEncodingError):
            failure = "connection interrupted"
        if attempt < attempts - 1:
            if delay:
                try:
                    delay = float(delay)
                except ValueError:
                    try:
                        delay = (parsedate_to_datetime(delay) - datetime.now(timezone.utc)).total_seconds()
                    except (TypeError, ValueError, OverflowError):
                        delay = 0
                if delay > 8:
                    break
            time.sleep(max(0, delay or 0, 0.3 * 2**attempt) + random.uniform(0, 0.15))
    raise OSError(f"upstream unavailable ({failure})")
