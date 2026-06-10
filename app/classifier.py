import json

from openai import AsyncOpenAI

from .config import settings
from .prompts import SYSTEM_PROMPT

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    # Lazy init so importing the app (e.g. --help, --test-telegram) doesn't
    # require an OpenAI key to be set.
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


async def classify(item: dict) -> dict | None:
    payload = {
        "headline": item.get("headline", ""),
        "summary": (item.get("summary") or "")[:600],
        "tagged_symbols": item.get("symbols") or [],
        "source": item.get("source", ""),
        "created_at": item.get("created_at", ""),
    }
    try:
        resp = await _get_client().chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload)},
            ],
            response_format={"type": "json_object"},
            timeout=15,
        )
        return _sanitize(json.loads(resp.choices[0].message.content))
    except Exception as exc:
        print(f"classification failed: {exc!r}")
        return None


def _sanitize(data: dict) -> dict:
    tickers = []
    for t in data.get("tickers") or []:
        if isinstance(t, dict) and t.get("symbol"):
            direction = t.get("direction", "unclear")
            if direction not in ("up", "down", "unclear"):
                direction = "unclear"
            tickers.append({"symbol": str(t["symbol"]).upper(), "direction": direction})
    try:
        score = max(0, min(100, int(data.get("score", 0))))
    except (TypeError, ValueError):
        score = 0
    return {
        "score": score,
        "tickers": tickers,
        "category": str(data.get("category", "other")),
        "rationale": str(data.get("rationale", ""))[:300],
    }
