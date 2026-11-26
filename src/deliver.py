"""TLS delivery and a durable receipt scoped to each published edition."""
from __future__ import annotations

import json
import logging
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from urllib.parse import urlsplit

import requests

from .archive import _atomic_write, day_path
from . import snapshot_render
from .brief import deals_enabled, recovery_note
from .guardrails import safe_http_url

logger = logging.getLogger(__name__)


class RetryableDeliveryError(RuntimeError):
    """The transport explicitly rejected the attempt before acceptance."""


def _slack_link(value, label):
    url = safe_http_url(value)
    if not url:
        return label
    url = url.replace("&", "&amp;").replace("<", "%3C").replace(">", "%3E").replace("|", "%7C")
    return f"<{url}|{label}>"


def _plain(text):
    return {"type": "section", "text": {"type": "plain_text", "text": text[:3000], "emoji": False}}


def send_slack(webhook_url: str, date_str: str, brief_items, price_moves, meta=None) -> None:
    if not safe_http_url(webhook_url) or not webhook_url.startswith("https://"):
        raise RetryableDeliveryError("Slack requires an HTTPS webhook")
    if urlsplit(webhook_url).hostname not in {"hooks.slack.com", "hooks.slack-gov.com"}:
        raise RetryableDeliveryError("Slack webhook host is invalid")
    meta = meta or {}
    title = f"Morning Deal Brief · {date_str}"
    fallback = title
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": title}}]
    if note := recovery_note(meta):
        fallback += "\n" + note
        blocks.append(_plain(note))
    if meta.get("outcome") != "complete":
        message = "Source coverage is " + meta.get("outcome", "unverified") + "."
        fallback += "\n" + message
        blocks.append(_plain(message))
    snap = meta.get("snapshot")
    if snap:
        snap_blocks, snap_text = snapshot_render.slack_blocks(snap)
        blocks.extend(snap_blocks)
        if snap_text:
            fallback += "\n\n" + snap_text[:1800]
        if not deals_enabled(meta):
            brief_items, price_moves = [], []
    if not brief_items and (not snap or deals_enabled(meta)):
        blocks.append(_plain("No events published for this edition."))
    for item in brief_items[:10]:
        heading = f"{item.rank}. {item.company[:220]} · {item.event_type.replace('_', ' ').title()}"
        blocks.append(_plain(f"{heading}\n{item.summary}\nAnalyst follow-up: {item.why_it_matters}"))
        source = safe_http_url(item.source_url)
        if source:
            blocks.append({"type": "context", "elements": [
                {"type": "plain_text", "text": (item.publisher or "Source")[:180]},
                {"type": "mrkdwn", "text": _slack_link(source, "Open evidence")},
            ]})
        # Notifications and screen readers may use only this plain-text fallback.
        # Include the entire excerpt and citation, or just the story heading.
        entry = f"\n{heading}"
        excerpt = f"{entry}\n{item.summary}\n{source}" if source else entry
        if len(fallback) + len(excerpt) <= 3900:
            fallback += excerpt
        elif len(fallback) + len(entry) <= 3900:
            fallback += entry
    if price_moves:
        session = price_moves[0].date
        market_lines = []
        for move in price_moves[:20]:
            change = "—" if move.pct_change is None else f"{move.pct_change:+.2f}%"
            provider = move.evidence.get("provider", "Source")
            link = _slack_link(move.evidence.get("source_url"), "data")
            market_lines.append(f"{move.ticker} {change} · {provider} · {link}")
        if len(price_moves) > 20:
            market_lines.append("Additional symbols are in the archived edition.")
        # Bound each section on whole lines so a source link is never clipped.
        blocks.append(_plain(f"Coverage prices · {session}"))
        for start in range(0, len(market_lines), 4):
            lines = [line for line in market_lines[start:start + 4] if len(line) < 730]
            if lines:
                blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}})
    try:
        response = requests.post(webhook_url, timeout=(4, 12), allow_redirects=False, json={
            "text": fallback, "mrkdwn": False, "blocks": blocks, "unfurl_links": False, "unfurl_media": False,
        })
    except requests.ConnectTimeout:
        raise RetryableDeliveryError("Slack connection timed out before submission") from None
    except requests.RequestException:
        raise RuntimeError("Slack acknowledgement was not received") from None
    if response.status_code == 200 and response.text.strip() == "ok":
        return
    if 400 <= response.status_code < 500:
        raise RetryableDeliveryError(f"Slack rejected the request (HTTP {response.status_code})")
    raise RuntimeError(f"Slack acknowledgement uncertain (HTTP {response.status_code})")


def send_email(smtp: dict, recipients: list, subject: str, body_plain: str, body_html: str) -> dict:
    sender = smtp.get("from") or smtp.get("user")
    if not sender:
        raise RetryableDeliveryError("SMTP_FROM or SMTP_USER is required")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    # Keep the coverage distribution private and pass explicit envelope recipients.
    msg["To"] = "undisclosed-recipients:;"
    msg.set_content(body_plain)
    msg.add_alternative(body_html, subtype="html")
    ctx = ssl.create_default_context()
    port = smtp.get("port", 587)
    server = None
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(smtp["host"], port, timeout=12, context=ctx)
        else:
            server = smtplib.SMTP(smtp["host"], port, timeout=12)
            server.starttls(context=ctx)
        if smtp.get("user"):
            server.login(smtp["user"], smtp["password"])
    except (smtplib.SMTPException, OSError):
        if server:
            server.close()
        raise RetryableDeliveryError("SMTP connection, TLS or authentication was rejected") from None
    try:
        return server.send_message(msg, to_addrs=recipients)
    except smtplib.SMTPRecipientsRefused as exc:
        return exc.recipients
    except (smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
        raise RetryableDeliveryError("SMTP envelope or DATA was rejected") from None
    finally:
        # A failed QUIT must not erase a positive DATA acknowledgement.
        try:
            server.close()
        except OSError:
            logger.debug("SMTP connection did not close cleanly")


def _receipt_path(cfg, date_str):
    return day_path(cfg.settings.archive_dir, date_str) / "delivery.json"


def read_receipt(cfg, date_str):
    try:
        return json.loads(_receipt_path(cfg, date_str).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def deliver(cfg, date_str, brief_md, brief_html, brief_items, price_moves, meta=None):
    s = cfg.settings
    meta = meta or {}
    path = _receipt_path(cfg, date_str)
    receipt = read_receipt(cfg, date_str) or {"revision": meta.get("revision"), "channels": {}}
    if receipt["revision"] != meta.get("revision"):
        raise RuntimeError("Delivery receipt refers to a different archive revision")
    channels = receipt["channels"]

    def save():
        receipt["updated_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_write(path, json.dumps(receipt, indent=2))

    issues = []
    if s.delivery in {"slack", "both"}:
        state = channels.get("slack")
        if state == "pending":
            issues.append("Slack acknowledgement uncertain")
        elif state != "accepted":
            if not s.slack_webhook_url:
                issues.append("Slack webhook is missing")
            else:
                channels["slack"] = "pending"
                save()
                try:
                    send_slack(s.slack_webhook_url, date_str, brief_items, price_moves, meta)
                    channels["slack"] = "accepted"
                except RetryableDeliveryError as exc:
                    channels["slack"] = "rejected"
                    issues.append(str(exc))
                except Exception:
                    issues.append("Slack acknowledgement uncertain")
                save()
    if s.delivery in {"email", "both"}:
        if not (s.smtp_host and s.email_recipients):
            issues.append("SMTP host or recipient list is missing")
        else:
            states = channels.setdefault("email", {})
            configured = list(dict.fromkeys(s.email_recipients))
            recipients = [p for p in configured if states.get(p) not in {"pending", "accepted"}]
            if any(states.get(p) == "pending" for p in configured):
                issues.append("SMTP acknowledgement uncertain")
            if recipients:
                states.update(dict.fromkeys(recipients, "pending"))
                save()
                smtp = {"host": s.smtp_host, "port": s.smtp_port, "user": s.smtp_user,
                        "password": s.smtp_password, "from": s.smtp_from}
                try:
                    refused = send_email(smtp, recipients, f"Morning Deal Brief · {date_str}", brief_md, brief_html)
                    states.update({p: "rejected" if p in refused else "accepted" for p in recipients})
                    if refused:
                        issues.append(f"SMTP rejected {len(refused)} recipient(s)")
                except RetryableDeliveryError as exc:
                    states.update(dict.fromkeys(recipients, "rejected"))
                    issues.append(str(exc))
                except Exception:
                    issues.append("SMTP acknowledgement uncertain")
                save()
    if issues:
        raise RuntimeError("Delivery incomplete. " + ". ".join(issues))
    logger.info("Delivery confirmed for %s", date_str)
