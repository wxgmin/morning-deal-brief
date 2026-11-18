"""Deliver the brief to Slack and/or email (both/either configurable)."""
from __future__ import annotations

import smtplib
from email.message import EmailMessage

import requests


def _event_label(event_type: str) -> str:
    return event_type.replace("_", " ").title()


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
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{b.rank}. {b.company} ({b.ticker}) — {_event_label(b.event_type)}*\n"
                        f"{b.summary}\n"
                        f"> *Why it matters:* {b.why_it_matters}"
                    ),
                },
            }
        )
        blocks.append(
            {"type": "context", "elements": [{"type": "mrkdwn", "text": f"<{b.source_url}|Source>"}]}
        )

    table_lines = ["*Coverage price moves*", "```"]
    for m in price_moves:
        flag = " *" if m.large_move else ""
        table_lines.append(f"{m.ticker:6} {m.pct_change:+6.2f}%  {m.z_score:+5.1f}σ{flag}")
    table_lines.append("```")
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(table_lines)}})

    payload = {"text": f"Morning Deal Brief — {date_str}", "blocks": blocks}
    resp = requests.post(webhook_url, json=payload, timeout=30)
    resp.raise_for_status()


def send_email(smtp: dict, recipients: list, subject: str, body_plain: str, body_html: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp["from"]
    msg["To"] = ", ".join(recipients)
    msg.set_content(body_plain)
    msg.add_alternative(body_html, subtype="html")

    port = smtp.get("port", 587)
    if port == 465:
        server = smtplib.SMTP_SSL(smtp["host"], port)
    else:
        server = smtplib.SMTP(smtp["host"], port)
        server.starttls()
    try:
        if smtp.get("user"):
            server.login(smtp["user"], smtp["password"])
        server.send_message(msg)
    finally:
        server.quit()


def deliver(cfg, date_str: str, brief_md: str, brief_html: str, brief_items, price_moves) -> None:
    s = cfg.settings
    if s.delivery in ("slack", "both") and s.slack_webhook_url:
        send_slack(s.slack_webhook_url, date_str, brief_items, price_moves)

    if s.delivery in ("email", "both") and s.smtp_host and s.email_recipients:
        smtp = {
            "host": s.smtp_host,
            "port": s.smtp_port,
            "user": s.smtp_user,
            "password": s.smtp_password,
            "from": s.smtp_from or s.smtp_user,
        }
        subject = f"Morning Deal Brief — {date_str}"
        send_email(smtp, s.email_recipients, subject, brief_md, brief_html)
