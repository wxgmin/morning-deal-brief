import pytest

from src import guardrails


def test_extract_figures_currency_and_percent():
    figs = guardrails.extract_figures("Revenue was $1.2 billion, up 8% with -3.20% operating margin.")
    assert figs == ["$1.2 billion", "8%", "-3.20%"]


def test_verify_a_complete_source_statement_and_editorial_follow_up():
    source = "The company announced a $500M acquisition and raised guidance to 8% growth."
    assert guardrails.verify_grounding(
        source, guardrails.FOLLOW_UPS["M&A"][0], ["$500M", "8%"], source
    ) == (True, [])


@pytest.mark.parametrize(("source", "claim"), [
    ("Revenue was $500M.", "Net debt was $500M."),
    ("Sales fell 8%.", "Sales rose 8%."),
    ("The company reported results.", "Closed 12 plants on 2030-01-01."),
    ("The company reported results.", "Debt is EUR 900; the multiple is 12x; growth is 8 percent."),
    ("The company reported results.", "The CEO resigned after fraud allegations."),
    ("The company employs 5 million people.", "Debt is $5 million."),
    ("The stock fell -3.20% on the day.", "The stock fell 3.2%."),
    ("The company expects to acquire Acme if antitrust clearance is granted.", "The company expects to acquire Acme"),
])
def test_different_claim_is_rejected_even_if_a_figure_matches(source, claim):
    assert guardrails.verify_grounding(claim, guardrails.FOLLOW_UPS["other"][0], [], source) == (False, [claim])


def test_unsupported_follow_up_and_citations_are_rejected():
    source = "The company reported quarterly results."
    grounded, failures = guardrails.verify_grounding(source, "Guidance raised.", ["$2.5B"], source)
    assert not grounded
    assert failures == ["Guidance raised.", "$2.5B"]
