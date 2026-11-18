"""LLM layer: pluggable providers plus grounded judgment and classification."""
from .core import (
    TAXONOMY,
    build_source_text,
    classify_text,
    fallback_judgment,
    judgment_for_event,
)
from .providers import NoneProvider, get_provider

__all__ = [
    "TAXONOMY",
    "build_source_text",
    "classify_text",
    "fallback_judgment",
    "judgment_for_event",
    "get_provider",
    "NoneProvider",
]
