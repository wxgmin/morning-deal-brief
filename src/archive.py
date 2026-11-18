"""Persist each day's brief to the archive directory."""
from __future__ import annotations

import json
from pathlib import Path


def save_brief(cfg, date_str: str, brief_md: str, brief_html: str, events_payload: dict) -> Path:
    day_dir = cfg.settings.archive_dir / date_str
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / "brief.md").write_text(brief_md, encoding="utf-8")
    (day_dir / "brief.html").write_text(brief_html, encoding="utf-8")
    (day_dir / "events.json").write_text(
        json.dumps(events_payload, indent=2, default=str), encoding="utf-8"
    )
    return day_dir
