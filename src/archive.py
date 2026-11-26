"""Dated archive with an atomic manifest and a cross-process job lock."""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path
from uuid import uuid4

from filelock import FileLock


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _validated_date(value: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("invalid archive date")
    return date.fromisoformat(value).isoformat()


def job_lock(cfg):
    cfg.settings.archive_dir.mkdir(parents=True, exist_ok=True)
    return FileLock(cfg.settings.archive_dir / ".pipeline.lock", timeout=0)


def day_path(root, date_str):
    return root / _validated_date(date_str)


def published_dates(root):
    dates = []
    if root:
        for manifest in root.glob("*/events.json"):
            try:
                dates.append(_validated_date(manifest.parent.name))
            except ValueError:
                continue
    return dates


def read_payload(root, date_str):
    try:
        data = json.loads((day_path(root, date_str) / "events.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(data, dict) or data.get("date") != date_str:
        raise ValueError("invalid archive manifest")
    return data


def save_brief(cfg, date_str: str, brief_md: str, brief_html: str, events_payload: dict) -> Path:
    day_dir = day_path(cfg.settings.archive_dir, date_str)
    revision = uuid4().hex
    target = day_dir / "runs" / revision
    target.mkdir(parents=True)
    _atomic_write(target / "brief.md", brief_md)
    _atomic_write(target / "brief.html", brief_html)
    _atomic_write(day_dir / "events.json", json.dumps(
        {**events_payload, "revision": revision}, indent=2, ensure_ascii=False, allow_nan=False
    ))
    return target


def artifacts(root, date_str, payload):
    day = day_path(root, date_str)
    revision = payload.get("revision")
    if revision is None:
        return day
    if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{32}", revision):
        raise ValueError("invalid archive revision")
    return day / "runs" / revision
