import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import AppConfig, CoverageConfig, Settings  # noqa: E402
from src.models import Company  # noqa: E402


@pytest.fixture
def cfg(tmp_path):
    client = Company("Graco Inc.", "GGG", role="client")
    peers = [
        Company("IDEX Corporation", "IEX", role="peer"),
        Company("Nordson Corporation", "NDSN", role="peer"),
    ]
    coverage = CoverageConfig(
        sector="Industrial / Fluid Handling",
        client=client,
        peers=peers,
        event_weights={
            "M&A": 1.0, "activist": 0.95, "rating_change": 0.9, "financing": 0.85,
            "management_change": 0.8, "guidance_change": 0.75, "earnings": 0.7,
            "large_price_move": 0.65, "other": 0.2,
        },
        relevance_weights={"client": 1.0, "peer": 0.8, "sector": 0.5},
        rss_feeds=[],
    )
    settings = Settings(openai_api_key=None, delivery="none", archive_dir=tmp_path / "archive")
    return AppConfig(settings=settings, coverage=coverage)
