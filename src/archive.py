"""Persist each day's brief to the archive directory (atomic writes)."""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _validated_date(date_str: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
        raise ValueError(f"invalid date_str: {date_str!r}")
    return date.fromisoformat(date_str).isoformat()


def save_brief(cfg, date_str: str, brief_md: str, brief_html: str, events_payload: dict) -> Path:
    date_str = _validated_date(date_str)
    day_dir = cfg.settings.archive_dir / date_str
    day_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(day_dir / "brief.md", brief_md)
    _atomic_write(day_dir / "brief.html", brief_html)
    _atomic_write(day_dir / "events.json", json.dumps(events_payload, indent=2, default=str))
    return day_dir
