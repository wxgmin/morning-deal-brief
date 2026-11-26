"""Local coverage dashboard and archive reader."""
from __future__ import annotations

import hmac
import json
import os
import secrets
import time
from dataclasses import asdict
from datetime import date as calendar_date
from io import BytesIO
from ipaddress import ip_address
from pathlib import Path
from threading import Lock, Thread
from urllib.parse import urlsplit

from filelock import Timeout
from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, session, url_for

from src import archive, catalog, chatgpt, config, deliver, profile, snapshot_render
from src import brief
from src.brief import change, event_label, number, recovery_note
from src.collectors.macro import search_companies
from src.guardrails import safe_http_url
from src.main import run_pipeline, today_iso
from src.models import BriefItem, PriceMove


def _date_label(value):
    return calendar_date.fromisoformat(value).strftime("%a, %b %d").replace(" 0", " ")


def _edition_label(value):
    return calendar_date.fromisoformat(value).strftime("%A, %B %d, %Y").replace(" 0", " ")


def create_app(cfg=None):
    app = Flask(__name__)
    cfg = cfg or config.load_config()
    app.secret_key = os.getenv("DASHBOARD_SECRET_KEY") or secrets.token_hex(32)
    app.config.update(
        TRUSTED_HOSTS=["127.0.0.1", "localhost", "[::1]"], SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax", MAX_CONTENT_LENGTH=4096,
    )
    app.jinja_env.filters["safeurl"] = safe_http_url
    app.jinja_env.globals.update(event_label=event_label, date_label=_date_label, edition_label=_edition_label,
                                 number=number, change=change, recovery_note=recovery_note)
    app.jinja_env.globals.update(snapshot_render.HELPERS)
    oauth = chatgpt.Connection(cfg.settings)
    oauth_lock = Lock()
    attempts = {}
    job_lock = Lock()
    job = {"state": "idle"}
    labels = {"filings": "SEC filings", "news": "Issuer news", "market": "Issuer prices",
              "snapshot": "Market briefing"}

    def session_token():
        if "run_token" not in session:
            session["run_token"] = secrets.token_urlsafe(32)
        return session["run_token"]

    def require_write():
        origin = request.headers.get("Origin")
        expected = urlsplit(request.host_url)
        token = session.get("run_token") or ""
        given = request.headers.get("X-Run-Token") or ""
        if (origin and origin != f"{expected.scheme}://{expected.netloc}") or (
            not token or not given.isascii() or not hmac.compare_digest(token, given) or not request.is_json
        ):
            abort(403)

    def desk_profile():
        return profile.load_profile(cfg.settings)

    def oauth_loopback():
        try:
            return urlsplit(request.host_url).hostname == "127.0.0.1" and ip_address(request.remote_addr).is_loopback
        except ValueError:
            return False

    def roots():
        return [root for root in (cfg.settings.archive_dir, cfg.settings.demo_archive_dir) if root]

    def dates_available():
        return sorted({day for root in roots() for day in archive.published_dates(root)}, reverse=True)

    def find_edition(day):
        for root in roots():
            payload = archive.read_payload(root, day)
            if payload is not None:
                return root, payload
        return None, {}

    def render(day):
        available = dates_available()
        selected = day or (available[0] if available else today_iso(cfg.settings.timezone))
        try:
            root, payload = find_edition(selected)
        except (OSError, ValueError):
            abort(503, "This archive edition could not be read.")
        if day and not payload:
            abort(404)
        schema = payload.get("schema_version")
        items = payload.get("items", [])
        moves = {m["ticker"]: m for m in payload.get("price_moves", [])}
        preferences = desk_profile()
        live_coverage = config.coverage_from_profile(cfg.settings, preferences) if cfg.profile_managed else cfg.coverage
        companies = payload.get("coverage", [asdict(c) for c in live_coverage.companies])
        sources = payload.get("sources", {})
        watch = [(c, moves.get(c["ticker"])) for c in companies]
        if root == cfg.settings.archive_dir:
            receipt = deliver.read_receipt(cfg, selected) or {}
        else:
            receipt = {}
        channels = receipt.get("channels", {})
        channel_states = [channels.get("slack"), *channels.get("email", {}).values()]
        delivery_status = (
            "Delivery needs attention" if "rejected" in channel_states or "pending" in channel_states
            else "Delivery accepted" if "accepted" in channel_states else ""
        )
        position = available.index(selected) if selected in available else 0
        nearby = available[max(0, position - 10):position + 11]
        edition_index = {}
        for candidate in nearby:
            try:
                _, data = find_edition(candidate)
                stories = data.get("items", [])
                edition_index[candidate] = {"items": len(stories), "client": any(
                    item.get("role") == "client" for item in stories
                )}
            except (OSError, ValueError):
                continue
        return render_template(
            "dashboard.html", dates=available, selected=selected, edition=payload,
            mode=payload.get("mode", "legacy" if payload else "empty"),
            outcome=payload.get("outcome") or ("unverified" if payload else "no archive"),
            items=items, watch=watch, sources=sources, source_labels=labels, is_current_schema=schema == 2,
            sector=payload.get("sector") or live_coverage.sector, delivery=delivery_status, run_token=session_token(),
            timezone=payload.get("timezone") or cfg.settings.timezone, root=root, edition_index=edition_index,
            preferences=preferences, market_labels={m["id"]: m["label"] for m in catalog.MARKETS},
            show_deals=brief.deals_enabled(payload),
            today=today_iso(cfg.settings.timezone),
            profile_changed=bool(edition_profile := payload.get("profile_revision"))
            and edition_profile != profile.revision(preferences),
        )

    @app.after_request
    def secure(response):
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'self'; script-src 'self'; connect-src 'self'; font-src 'self'; "
            "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def index():
        return render(None)

    @app.get("/brief/<day>")
    def brief_view(day):
        try:
            archive._validated_date(day)
        except ValueError:
            abort(404)
        return render(day)

    @app.get("/brief/<day>/download/<kind>")
    def download(day, kind):
        if kind not in {"md", "html", "json"}:
            abort(404)
        try:
            root, payload = find_edition(archive._validated_date(day))
            if not root:
                abort(404)
            if kind == "json":
                buf = BytesIO(json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8"))
                return send_file(buf, as_attachment=True, download_name=f"morning-brief-{day}.json")
            path = archive.artifacts(root, day, payload) / f"brief.{kind}"
            if not path.is_file():
                if root != cfg.settings.demo_archive_dir or payload.get("schema_version") != 2:
                    abort(404)
                items = [BriefItem(**item) for item in payload["items"]]
                moves = [PriceMove(**row) for row in payload["price_moves"]]
                renderer = brief.render_html if kind == "html" else brief.render_markdown
                buf = BytesIO(renderer(items, moves, cfg.coverage, day, payload).encode("utf-8"))
                return send_file(buf, as_attachment=True, download_name=f"morning-brief-{day}.{kind}")
            return send_file(path.resolve(), as_attachment=True, download_name=f"morning-brief-{day}.{kind}")
        except ValueError:
            abort(404)

    @app.get("/api/dates")
    def api_dates():
        return jsonify(dates_available())

    @app.get("/api/coverage")
    def api_coverage():
        coverage = config.coverage_from_profile(cfg.settings) if cfg.profile_managed else cfg.coverage
        return jsonify(sector=coverage.sector, companies=[
            {"name": c.name, "ticker": c.ticker, "role": c.role} for c in coverage.companies
        ])

    @app.get("/settings")
    def settings_view():
        return render_template("settings.html", preferences=desk_profile(), catalog=catalog,
                               managed=cfg.profile_managed, run_token=session_token(),
                               chatgpt=oauth.public_state(), auth_result=session.pop("chatgpt_result", None),
                               loopback=oauth_loopback(),
                               loopback_settings=f"http://127.0.0.1:{urlsplit(request.host_url).port or 80}/settings#chatgpt")

    @app.post("/api/chatgpt/sign-in")
    def chatgpt_start():
        require_write()
        host = urlsplit(request.host_url)
        if not oauth_loopback():
            return jsonify(message="Open Preferences at 127.0.0.1 to connect your account."), 400
        # Build the exact loopback redirect from the trusted local host and active port.
        callback = f"http://127.0.0.1:{host.port or 80}{chatgpt.CALLBACK}"
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(message="Invalid sign-in request."), 400
        client_id = body.get("client_id")
        if client_id is not None and not isinstance(client_id, str):
            return jsonify(message="Choose a saved ChatGPT account."), 400
        try:
            target, pending = oauth.start(callback, client_id)
        except Exception:
            return jsonify(message="Could not start ChatGPT sign-in. Check local credential storage."), 503
        browser = session.get("run_token")
        with oauth_lock:
            for key in list(attempts):
                if time.monotonic() - attempts[key]["started"] > 600:
                    del attempts[key]
            attempts[browser] = pending
        return jsonify(url=target)

    @app.get(chatgpt.CALLBACK)
    def chatgpt_callback():
        if not oauth_loopback():
            abort(403)
        browser = session.get("run_token")
        supplied = request.args.get("state", "")
        with oauth_lock:
            pending = attempts.get(browser)
            if (not pending or time.monotonic() - pending["started"] > 600
                    or not supplied.isascii() or not hmac.compare_digest(pending["state"], supplied)):
                abort(400, "Start a new ChatGPT sign-in from Preferences.")
            del attempts[browser]
        try:
            first = oauth.finish(pending, request.args)
            session["chatgpt_result"] = "first" if first else "connected"
        except chatgpt.ChatGPTError as exc:
            session["chatgpt_result"] = "denied" if exc.code == "denied" else (
                "ineligible" if exc.brief_status == "ineligible" else "failed")
        except Exception:
            session["chatgpt_result"] = "failed"
        return redirect(url_for("settings_view") + "#chatgpt", code=303)

    @app.post("/api/chatgpt/sign-out")
    def chatgpt_signout():
        require_write()
        with oauth_lock:
            attempts.pop(session.get("run_token"), None)
        try:
            confirmed = oauth.sign_out()
        except Exception:
            return jsonify(message="Could not sign out of the local ChatGPT account."), 503
        session["chatgpt_result"] = "disconnected" if confirmed else "revoke_unconfirmed"
        return jsonify(url=url_for("settings_view") + "#chatgpt")

    @app.get("/api/profile")
    def api_profile():
        preferences = desk_profile()
        return jsonify(profile=preferences, revision=profile.revision(preferences))

    @app.put("/api/profile")
    def update_profile():
        require_write()
        # Profile writes have their own limit. Other dashboard writes keep the 4 KB cap.
        request.max_content_length = 256 * 1024
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get("revision"), str):
            return jsonify(message="Reload preferences before saving."), 400
        try:
            preferences = profile.save_profile(data.get("profile"), cfg.settings, expected=data["revision"])
        except profile.ProfileConflict as exc:
            return jsonify(message=str(exc)), 409
        except profile.ProfileError as exc:
            return jsonify(message=str(exc)), 400
        except (Timeout, OSError):
            return jsonify(message="Preferences could not be saved. Try again."), 503
        return jsonify(profile=preferences, revision=profile.revision(preferences))

    @app.post("/api/profile/apply")
    def apply_setup():
        """Preview a role or watchlist preset on the unsaved form. Nothing is written to disk."""
        require_write()
        request.max_content_length = 256 * 1024
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get("profile"), dict):
            return jsonify(message="Reload preferences and try again."), 400
        try:
            current = profile.validate_profile(data["profile"])
            if data.get("role"):
                updated, changes = profile.apply_role(current, str(data["role"]))
            elif data.get("preset"):
                updated = profile.validate_profile(profile.apply_preset(current, str(data["preset"])))
                changes = [f"Watchlist replaced with {len(updated['watchlist'])} names: "
                           + ", ".join(row["ticker"] for row in updated["watchlist"])]
            else:
                return jsonify(message="Choose a role or a watchlist."), 400
        except profile.ProfileError as exc:
            return jsonify(message=str(exc)), 400
        return jsonify(profile=updated, changes=changes)

    @app.get("/api/chart")
    def chart_data():
        """Read-only price history for the chart popup; validated symbol and range, fixed upstream host."""
        from src.collectors import macro as macro_mod
        try:
            return jsonify(macro_mod.history(request.args.get("symbol", ""), request.args.get("range", "1y")))
        except ValueError:
            return jsonify(message="Unknown symbol or range."), 400
        except OSError:
            return jsonify(message="Price history is unavailable right now."), 503

    @app.post("/api/ask")
    def ask_brief():
        """Answer a question from one archived edition's facts; the answer is fact-checked before it is returned."""
        require_write()
        data = request.get_json(silent=True) or {}
        try:
            day = archive._validated_date(str(data.get("date") or ""))
        except ValueError:
            return jsonify(state="invalid", message="Choose an edition first."), 400
        _, payload = find_edition(day)
        snap = (payload or {}).get("snapshot")
        if not snap:
            return jsonify(state="invalid", message="This edition has no market snapshot to ask about."), 404
        from src import narrative, snapshot as snapshot_mod
        result = narrative.answer(snap, data.get("question"), snapshot_mod.ai_generator(cfg.settings))
        return jsonify(result), 200 if result["state"] in {"ok", "unanswerable", "rejected"} else 400 if result["state"] == "invalid" else 503

    @app.get("/api/companies")
    def company_search():
        query = request.args.get("q", "").strip()
        if not 2 <= len(query) <= 80:
            return jsonify(message="Enter 2 to 80 characters."), 400
        try:
            return jsonify(results=search_companies(query))
        except (ValueError, OSError):
            return jsonify(message="Company search is unavailable. You can still add a ticker manually."), 503

    def worker():
        day = today_iso(cfg.settings.timezone)
        try:
            run_pipeline(cfg, as_of_date=day, send=False)
            state = {"state": "done", "url": url_for("brief_view", day=day)}
        except Timeout:
            state = {"state": "busy", "message": "A scheduled brief is already running. Try again after it finishes."}
        except Exception as exc:
            app.logger.warning("Dashboard collection ended with %s", type(exc).__name__)
            state = {"state": "failed", "message": "Collection encountered an error. See the CLI logs or today’s coverage status."}
            if (Path(cfg.settings.archive_dir) / day / "events.json").is_file():
                state["url"] = url_for("brief_view", day=day)
        with job_lock:
            job.update(state)

    @app.get("/api/run")
    def run_status():
        with job_lock:
            return jsonify(job)

    @app.post("/api/run")
    def api_run():
        require_write()
        with job_lock:
            if job["state"] == "running":
                return jsonify(state="running"), 202
            job.clear()
            job["state"] = "running"

        def process():
            # Keep endpoint generation independent of any user-supplied Host headers.
            with app.test_request_context():
                worker()

        try:
            Thread(target=process, daemon=True, name="brief-collection").start()
        except RuntimeError:
            with job_lock:
                job["state"] = "failed"
            return jsonify(state="failed", message="Collection could not start."), 503
        return jsonify(state="running"), 202

    return app


app = create_app()


if __name__ == "__main__":
    from waitress import serve

    serve(app, host="127.0.0.1", port=5000, threads=4)
