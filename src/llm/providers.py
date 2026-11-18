"""Pluggable LLM providers.

Each provider exposes a single ``chat_json(system, user, model) -> dict`` method.
OpenAI, Gemini, OpenRouter, and local OpenAI-compatible servers (Ollama, LM Studio)
share one implementation; Anthropic has its own; ``NoneProvider`` is the
deterministic no-LLM fallback.

Switching providers is a one-line config change: set ``LLM_PROVIDER`` (and the
matching key). ``auto`` (default) detects from whichever key is present.
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

GEMINI_OPENAI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
OLLAMA_BASE = "http://localhost:11434/v1"
LMSTUDIO_BASE = "http://localhost:1234/v1"

# (judgment model, classification model) defaults per provider.
DEFAULT_MODELS = {
    "openai": ("gpt-4o", "gpt-4o-mini"),
    "anthropic": ("claude-3-5-sonnet-latest", "claude-3-5-haiku-latest"),
    "gemini": ("gemini-2.5-pro", "gemini-2.5-flash"),
    "openrouter": (None, None),
    "local": (None, None),
}

_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


def _extract_json(text: str) -> dict:
    """Return a dict from an LLM response, tolerating fences/arrays. Raises if invalid."""
    text = (text or "").strip()
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for cand in candidates:
        cand = _FENCE_RE.sub("", cand.strip()).strip()
        try:
            obj = json.loads(cand)
        except Exception:
            continue
        if isinstance(obj, dict):
            return obj
        if isinstance(obj, list) and obj and isinstance(obj[0], dict):
            return obj[0]

    raise ValueError("LLM did not return a valid JSON object")


class NoneProvider:
    name = "none"

    def chat_json(self, system, user, model):
        raise RuntimeError("No LLM provider configured")


class OpenAICompatProvider:
    """Any OpenAI-compatible /chat/completions endpoint (OpenAI, Gemini, OpenRouter, local)."""

    def __init__(self, name, api_key, base_url=None):
        self.name = name
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=120.0, max_retries=2)

    def chat_json(self, system, user, model):
        resp = self._client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
        )
        return _extract_json(resp.choices[0].message.content or "")


class AnthropicProvider:
    def __init__(self, api_key):
        self.name = "anthropic"
        from anthropic import Anthropic

        self._client = Anthropic(api_key=api_key, timeout=120.0, max_retries=2)

    def chat_json(self, system, user, model):
        resp = self._client.messages.create(
            model=model,
            max_tokens=4096,
            system=system + "\n\nRespond with a single JSON object and nothing else.",
            messages=[{"role": "user", "content": user}],
        )
        if getattr(resp, "stop_reason", None) == "max_tokens":
            raise RuntimeError("Anthropic response truncated by max_tokens")
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return _extract_json(text)


def _auto_detect(s) -> str:
    if s.anthropic_api_key:
        return "anthropic"
    if s.gemini_api_key:
        return "gemini"
    if s.openai_api_key:
        return "openai"
    if s.llm_base_url:
        return "local"
    if s.llm_api_key:
        return "openai"
    return "none"


def get_provider(cfg):
    s = cfg.settings
    p = (s.llm_provider or "auto").strip().lower()
    if p == "auto":
        p = _auto_detect(s)
    if p == "none":
        return NoneProvider()

    generic_key = s.llm_api_key

    if p == "openai":
        key = s.openai_api_key or generic_key
        return OpenAICompatProvider("openai", key) if key else NoneProvider()
    if p == "anthropic":
        key = s.anthropic_api_key or generic_key
        return AnthropicProvider(key) if key else NoneProvider()
    if p == "gemini":
        key = s.gemini_api_key or generic_key
        return OpenAICompatProvider("gemini", key, base_url=GEMINI_OPENAI_BASE) if key else NoneProvider()
    if p == "openrouter":
        key = generic_key or s.openai_api_key
        return OpenAICompatProvider("openrouter", key, base_url=OPENROUTER_BASE) if key else NoneProvider()
    if p in ("local", "lmstudio"):
        base = s.llm_base_url or (LMSTUDIO_BASE if p == "lmstudio" else OLLAMA_BASE)
        return OpenAICompatProvider(p, s.llm_api_key or "local", base_url=base)

    logger.warning("Unknown LLM_PROVIDER %r; using no-LLM fallback", p)
    return NoneProvider()


def models_for(provider_name: str):
    return DEFAULT_MODELS.get(provider_name, (None, None))
