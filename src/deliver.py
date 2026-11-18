"""Deliver the brief to Slack and/or email (both/either configurable, isolated)."""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)

_SLACK_MAX_BLOCKS = 50
_SLACK_MAX_SECTION_CHARS = 3000


def _event_label(event_type: str) -> str:
    return event_type.replace("_", " ").title()


def _slack_escape(text) -> str:
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _safe_http_url(url: str) -> str:
    url = (url or "").strip()
    try:
        return url if urlsplit(url).scheme.lower() in ("http", "https") else ""
    except Exception:
        return ""


def _slack_link(url: str, label: str) -> str:
    url = _safe_http_url(url)
    if not url:
        return _slack_escape(label)
    url = url.replace("<", "%3C").replace(">", "%3E").replace("|", "%7C")
    return f"<{url}|{_slack_escape(label)}>"


def _post_slack(webhook_url: str, blocks: list, text: str) -> None:
    for i in range(0, len(blocks), _SLACK_MAX_BLOCKS):
        chunk = blocks[i : i + _SLACK_MAX_BLOCKS]
        payload = {"blocks": chunk}
        if i == 0:
            payload["text"] = text
        resp = requests.post(webhook_url, json=payload, timeout=30)
        resp.raise_for_status()
        # Slack's incoming webhook returns HTTP 200 even on app-level errors.
        body = resp.text.strip()
        if body and body != "ok":
            raise RuntimeError(f"Slack webhook error: {body}")


def send_slack(webhook_url: str, date_str: str, brief_items, price_moves) -> None:
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"Morning Deal Brief — {date_str}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "*What matters today*"}},
    ]

    if not brief_items:
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": "_No coverage-relevant events met the threshold._"}}
        )
    for b in brief_items:
        text = (
            f"*{b.rank}. {_slack_escape(b.company)} ({_slack_escape(b.ticker)}) — {_event_label(b.event_type)}*\n"
            f"{_slack_escape(b.summary)}\n"
            f"> *Why it matters:* {_slack_escape(b.why_it_matters)}"
        )[: _SLACK_MAX_SECTION_CHARS]
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text}})
        if b.source_url:
            blocks.append(
                {"type": "context", "elements": [{"type": "mrkdwn", "text": _slack_link(b.source_url, "Source")}]}
            )

    table_lines = ["*Coverage price moves*", "```"]
    for m in price_moves:
        flag = " *" if m.large_move else ""
        table_lines.append(f"{m.ticker:6} {m.pct_change:+6.2f}%  {m.z_score:+5.1f}σ{flag}")
    table_lines.append("```")
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(table_lines)}})

    _post_slack(webhook_url, blocks, f"Morning Deal Brief — {date_str}")


def send_email(smtp: dict, recipients: list, subject: str, body_plain: str, body_html: str) -> None:
    sender = smtp.get("from") or smtp.get("user")
    if not sender:
        raise ValueError("SMTP_FROM or SMTP_USER is required for email delivery")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(body_plain)
    msg.add_alternative(body_html, subtype="html")

    ctx = ssl.create_default_context()
    port = smtp.get("port", 587)
    if port == 465:
        server = smtplib.SMTP_SSL(smtp["host"], port, timeout=30, context=ctx)
    else:
        server = smtplib.SMTP(smtp["host"], port, timeout=30)
    try:
        if port != 465:
            server.starttls(context=ctx)
        if smtp.get("user"):
            server.login(smtp["user"], smtp["password"])
        server.send_message(msg)
    finally:
        server.quit()


def deliver(cfg, date_str: str, brief_md: str, brief_html: str, brief_items, price_moves) -> None:
    s = cfg.settings
    errors = []

    if s.delivery in ("slack", "both"):
        if not s.slack_webhook_url:
            logger.warning("DELIVERY includes slack but SLACK_WEBHOOK_URL is not set; skipping Slack")
            errors.append("slack: SLACK_WEBHOOK_URL not set")
        else:
            try:
                send_slack(s.slack_webhook_url, date_str, brief_items, price_moves)
            except Exception as exc:
                logger.warning("Slack delivery failed: %s", exc, exc_info=True)
                errors.append(f"slack: {exc}")

    if s.delivery in ("email", "both"):
        if not (s.smtp_host and s.email_recipients):
            logger.warning("DELIVERY includes email but SMTP_HOST/EMAIL_RECIPIENTS are not set; skipping email")
            errors.append("email: SMTP_HOST/EMAIL_RECIPIENTS not set")
        else:
            smtp = {
                "host": s.smtp_host,
                "port": s.smtp_port,
                "user": s.smtp_user,
                "password": s.smtp_password,
                "from": s.smtp_from or s.smtp_user,
            }
            try:
                send_email(smtp, s.email_recipients, f"Morning Deal Brief — {date_str}", brief_md, brief_html)
            except Exception as exc:
                logger.warning("Email delivery failed: %s", exc, exc_info=True)
                errors.append(f"email: {exc}")

    if errors:
        raise RuntimeError("Delivery failed: " + "; ".join(errors))
