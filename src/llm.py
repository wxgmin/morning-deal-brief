"""OpenAI wrapper for classification and grounded judgment, plus a deterministic
fallback that keeps the pipeline runnable (and always grounded) without a key."""
from __future__ import annotations

import json

from .guardrails import verify_grounding
from .models import Event, Judgment

TAXONOMY = [
    "M&A", "earnings", "guidance_change", "financing", "management_change",
    "activist", "rating_change", "large_price_move", "other",
]


def _client(cfg):
    from openai import OpenAI

    return OpenAI(api_key=cfg.settings.openai_api_key)


def chat_json(cfg, system: str, user: str, model: str = None) -> dict:
    client = _client(cfg)
    resp = client.chat.completions.create(
        model=model or cfg.settings.openai_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    return json.loads(resp.choices[0].message.content or "{}")


def classify_text(cfg, title: str, text: str) -> dict:
    system = (
        "You classify a corporate/financial event into exactly one category. "
        "Return JSON with keys 'event_type' and 'confidence' (0-1). "
        f"Allowed event_type values: {', '.join(TAXONOMY)}."
    )
    user = f"Title: {title}\n\nText:\n{text[:2500]}"
    return chat_json(cfg, system, user, model=cfg.settings.openai_classify_model)


def _coverage_context(coverage) -> str:
    peers = ", ".join(f"{p.name} ({p.ticker})" for p in coverage.peers)
    return (
        f"Client: {coverage.client.name} ({coverage.client.ticker}). "
        f"Peers: {peers}. Sector: {coverage.sector}."
    )


def _judgment_system() -> str:
    return (
        "You are a banking coverage analyst writing a two-line summary and a one-line "
        "'why it matters for us' note. You may state ONLY facts present in the provided "
        "SOURCE TEXT. Never introduce a figure, percentage, dollar amount, date, or count "
        "that is not verbatim in the SOURCE TEXT. If a figure is not present, omit it. "
        "Return JSON with keys: 'summary' (string, at most 2 sentences), "
        "'why_it_matters' (string, exactly 1 sentence), "
        "'figures_cited' (array of every number/percentage/dollar amount you used, "
        "copied verbatim from the source), and 'confidence' (float 0-1)."
    )


def write_judgment(cfg, event: Event, source_text: str, coverage, omit: list = None) -> Judgment:
    item = event.item
    user = (
        f"Coverage context: {_coverage_context(coverage)}\n"
        f"Event type: {event.event_type}. Company: {item.company} ({item.ticker}).\n\n"
        f"SOURCE TEXT:\n{source_text}"
    )
    if omit:
        user += f"\n\nDo NOT mention these figures (they are not in the source): {', '.join(omit)}."

    data = chat_json(cfg, _judgment_system(), user, model=cfg.settings.openai_model)
    return Judgment(
        summary=str(data.get("summary", "")).strip(),
        why_it_matters=str(data.get("why_it_matters", "")).strip(),
        figures_cited=[str(f) for f in data.get("figures_cited", [])],
        confidence=float(data.get("confidence", 0.5)),
    )


def fallback_judgment(event: Event) -> Judgment:
    """Deterministic, metadata-only judgment (no figures, therefore always grounded)."""
    item = event.item
    company = item.company or item.ticker
    event_type = event.event_type.replace("_", " ").title()

    if item.source == "market":
        z = abs(float(item.extra.get("z_score", 0.0)))
        summary = f"{item.ticker} moved {item.extra.get('pct_change', 0):+.1f}% ({z:.1f} sigma) on {item.published}."
        why = f"A {z:.1f}-sigma move in a {item.role} name is worth flagging to the team this morning."
    elif item.form:
        summary = f"{company} filed {item.form} ({event.event_type}) on {item.published}."
        why = f"{event_type} at a {item.role} is a potential banking angle or read-through for the coverage list."
    else:
        summary = item.title
        why = f"{event_type} at a {item.role} is a potential banking angle or read-through for the coverage list."

    return Judgment(summary=summary, why_it_matters=why, figures_cited=[], confidence=0.5, grounded=True)


def build_source_text(event: Event, source_text: str) -> str:
    """Prepend a metadata header so date/ticker/form references are always groundable."""
    item = event.item
    header = f"{item.company} ({item.ticker}) — {item.source} item dated {item.published}"
    if item.form:
        header += f", form {item.form}"
    header += "."
    return f"{header}\n\n{source_text or item.summary or item.title}"


def judgment_for_event(cfg, event: Event, source_text: str, coverage) -> Judgment:
    """Produce a grounded judgment. LLM with a grounding retry when a key is present,
    otherwise the deterministic metadata-only fallback."""
    full_source = build_source_text(event, source_text)

    if not cfg.settings.llm_enabled:
        return fallback_judgment(event)

    try:
        j = write_judgment(cfg, event, full_source, coverage)
        grounded, ungrounded = verify_grounding(j.summary, j.why_it_matters, j.figures_cited, full_source)
        if not grounded:
            j2 = write_judgment(cfg, event, full_source, coverage, omit=ungrounded)
            grounded, ungrounded = verify_grounding(j2.summary, j2.why_it_matters, j2.figures_cited, full_source)
            j2.grounded = grounded
            j2.ungrounded_figures = ungrounded
            return j2
        j.grounded = True
        return j
    except Exception:
        return fallback_judgment(event)
