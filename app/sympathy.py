"""Second-order plays: when a stock moves, its basket peers move minutes later."""

from pathlib import Path

import yaml

BASKETS_PATH = Path(__file__).resolve().parent.parent / "data" / "baskets.yaml"
SYMPATHY_CAP = 6

_baskets: dict[str, list[str]] | None = None


def _load() -> dict[str, list[str]]:
    global _baskets
    if _baskets is None:
        _baskets = yaml.safe_load(BASKETS_PATH.read_text()) or {}
    return _baskets


def sympathy_for(symbols: list[str], cap: int = SYMPATHY_CAP) -> list[str]:
    """Union of basket members sharing any basket with the alerted symbols,
    minus the alerted symbols themselves, capped."""
    alerted = set(symbols)
    out: list[str] = []
    for members in _load().values():
        if alerted & set(members):
            for m in members:
                if m not in alerted and m not in out:
                    out.append(m)
    return out[:cap]


def merge_sympathy(symbols: list[str], llm_suggestions: list[str],
                   cap: int = SYMPATHY_CAP) -> list[str]:
    """Curated baskets first, then LLM suggestions, deduped and capped."""
    alerted = set(symbols)
    out = sympathy_for(symbols, cap=cap)
    for s in llm_suggestions:
        if s not in alerted and s not in out:
            out.append(s)
    return out[:cap]
