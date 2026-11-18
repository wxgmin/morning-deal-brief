"""High-level LLM tasks: classification, grounded judgment, and the no-LLM fallback."""
from __future__ import annotations

from ..guardrails import verify_grounding
from ..models import Event, Judgment
from .providers import NoneProvider, get_provider, models_for

TAXONOMY = [
    "M&A", "earnings", "guidance_change", "financing", "management_change",
    "activist", "rating_change", "large_price_move", "other",
]


def _resolve_models(cfg, provider):
    judge_default, classify_default = models_for(provider.name)
    judge = cfg.settings.llm_model or judge_default
    classify = cfg.settings.llm_classify_model or classify_default
    return judge, classify


def classify_text(cfg, title: str, text: str) -> dict:
    provider = get_provider(cfg)
    if isinstance(provider, NoneProvider):
        raise RuntimeError("no LLM provider configured")
    _, classify_model = _resolve_models(cfg, provider)
    if not classify_model:
        raise RuntimeError("no classification model configured")

    system = (
        "You classify a corporate/financial event into exactly one category. "
        "Return a JSON object with keys 'event_type' and 'confidence' (0-1). "
        f"Allowed event_type values: {', '.join(TAXONOMY)}."
    )
    user = f"Title: {title}\n\nText:\n{text[:2500]}"
    return provider.chat_json(system, user, classify_model)


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
        "Return a JSON object with keys: 'summary' (string, at most 2 sentences), "
        "'why_it_matters' (string, exactly 1 sentence), "
        "'figures_cited' (array of every number/percentage/dollar amount you used, "
        "copied verbatim from the source), and 'confidence' (float 0-1)."
    )


def write_judgment(cfg, provider, model, event, source_text, coverage, omit=None) -> Judgment:
    item = event.item
    user = (
        f"Coverage context: {_coverage_context(coverage)}\n"
        f"Event type: {event.event_type}. Company: {item.company} ({item.ticker}).\n\n"
        f"SOURCE TEXT:\n{source_text}"
    )
    if omit:
        user += f"\n\nDo NOT mention these figures (they are not in the source): {', '.join(omit)}."

    data = provider.chat_json(_judgment_system(), user, model)
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
    """Produce a grounded judgment: LLM with a grounding retry when a provider is
    configured, otherwise the deterministic metadata-only fallback."""
    full_source = build_source_text(event, source_text)
    provider = get_provider(cfg)
    if isinstance(provider, NoneProvider):
        return fallback_judgment(event)

    judge_model, _ = _resolve_models(cfg, provider)
    if not judge_model:
        return fallback_judgment(event)

    try:
        j = write_judgment(cfg, provider, judge_model, event, full_source, coverage)
        grounded, ungrounded = verify_grounding(j.summary, j.why_it_matters, j.figures_cited, full_source)
        if not grounded:
            j2 = write_judgment(cfg, provider, judge_model, event, full_source, coverage, omit=ungrounded)
            grounded, ungrounded = verify_grounding(j2.summary, j2.why_it_matters, j2.figures_cited, full_source)
            j2.grounded = grounded
            j2.ungrounded_figures = ungrounded
            return j2
        j.grounded = True
        return j
    except Exception:
        return fallback_judgment(event)
