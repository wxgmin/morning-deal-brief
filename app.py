"""Web dashboard for the Morning Deal Brief.

Run:  python app.py   (then open http://127.0.0.1:5000)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from flask import Flask, abort, jsonify, render_template

from src import config
from src.main import run_pipeline, today_iso

_EVENT_CLASSES = {
    "M&A": "ma",
    "earnings": "earnings",
    "guidance_change": "guidance",
    "financing": "financing",
    "management_change": "mgmt",
    "activist": "activist",
    "rating_change": "rating",
    "large_price_move": "move",
    "other": "other",
}


def _event_label(event_type: str) -> str:
    return (event_type or "other").replace("_", " ").title()


def _event_class(event_type: str) -> str:
    return _EVENT_CLASSES.get(event_type, "other")


def _archive_dates(archive_dir: Path) -> list:
    if not archive_dir.exists():
        return []
    dates = [
        d.name for d in archive_dir.iterdir()
        if d.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.name)
    ]
    return sorted(dates, reverse=True)


def _load_events(archive_dir: Path, date_str: str) -> dict:
    path = archive_dir / date_str / "events.json"
    if not path.exists():
        return {"date": date_str, "items": [], "price_moves": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"date": date_str, "items": [], "price_moves": []}


def create_app():
    app = Flask(__name__)
    app.jinja_env.globals["event_label"] = _event_label
    app.jinja_env.globals["event_class"] = _event_class

    cfg = config.load_config()

    def _render(date_str):
        dates = _archive_dates(cfg.settings.archive_dir)
        selected = date_str or (dates[0] if dates else today_iso(cfg.settings.timezone))
        events = _load_events(cfg.settings.archive_dir, selected)
        return render_template(
            "dashboard.html",
            dates=dates,
            selected=selected,
            sector=cfg.coverage.sector,
            items=events.get("items", []),
            price_moves=events.get("price_moves", []),
            coverage=[{"name": c.name, "ticker": c.ticker, "role": c.role} for c in cfg.coverage.companies],
            delivery=cfg.settings.delivery,
            provider=cfg.settings.llm_provider,
        )

    @app.route("/")
    def index():
        return _render(None)

    @app.route("/brief/<date_str>")
    def brief_view(date_str):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
            abort(404)
        return _render(date_str)

    @app.route("/api/dates")
    def api_dates():
        return jsonify(_archive_dates(cfg.settings.archive_dir))

    @app.route("/api/coverage")
    def api_coverage():
        return jsonify({
            "sector": cfg.coverage.sector,
            "companies": [
                {"name": c.name, "ticker": c.ticker, "role": c.role} for c in cfg.coverage.companies
            ],
        })

    @app.route("/api/run", methods=["POST"])
    def api_run():
        try:
            items, moves = run_pipeline(cfg, include_news=True, send=False)
            return jsonify({"ok": True, "items": len(items), "moves": len(moves)})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
