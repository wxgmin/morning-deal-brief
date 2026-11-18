from src import guardrails


def test_extract_figures_currency_and_percent():
    text = "Revenue was $1.2 billion, up 8% with -3.20% operating margin."
    figs = guardrails.extract_figures(text)
    assert "$1.2 billion" in figs
    assert "8%" in figs
    assert "-3.20%" in figs


def test_verify_grounding_passes_for_verbatim_figures():
    source = "The company announced a $500M acquisition and raised guidance to 8% growth."
    grounded, ungrounded = guardrails.verify_grounding(
        "Announced a $500M deal and raised guidance to 8%.",
        "Acquisition at a peer is a potential buyer conversation.",
        figures_cited=["$500M", "8%"],
        source_text=source,
    )
    assert grounded
    assert ungrounded == []


def test_verify_grounding_fails_for_hallucinated_figure():
    source = "The company reported quarterly results."
    grounded, ungrounded = guardrails.verify_grounding(
        "Reported a $2.5B revenue beat.",
        "Guidance raised.",
        figures_cited=["$2.5B"],
        source_text=source,
    )
    assert not grounded
    assert "$2.5B" in ungrounded


def test_verify_grounding_tolerates_percent_formatting():
    source = "The stock fell -3.20% on the day."
    grounded, _ = guardrails.verify_grounding(
        "The stock fell 3.2%.",
        "A large move.",
        figures_cited=[],
        source_text=source,
    )
    assert grounded


def test_verify_grounding_ignores_bare_numbers():
    # "Q4" contains a bare digit that should not trip the figure backstop.
    source = "The company reported fourth quarter results."
    grounded, ungrounded = guardrails.verify_grounding(
        "Reported Q4 results.",
        "Earnings for a peer.",
        figures_cited=[],
        source_text=source,
    )
    assert grounded
