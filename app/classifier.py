import json

from anthropic import AsyncAnthropic

from .config import settings
from .events import Event
from .prompts import SYSTEM_PROMPT

_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    # Lazy init so importing the app (e.g. --help, --test-telegram) doesn't
    # require an Anthropic key to be set.
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


async def classify(event: Event) -> dict | None:
    payload = {
        "headline": event.text,
        "summary": (event.meta.get("summary") or "")[:600],
        "tagged_symbols": event.symbols,
        "source": event.meta.get("wire") or event.source,
        "created_at": event.meta.get("created_at", ""),
    }
    try:
        resp = await _get_client().messages.create(
            model=settings.anthropic_model,
            max_tokens=300,
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": json.dumps(payload)},
                # Prefill forces the model to emit raw JSON with no preamble.
                {"role": "assistant", "content": "{"},
            ],
            timeout=15,
        )
        raw = "{" + resp.content[0].text
        return _sanitize(json.loads(raw))
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
    primary = {t["symbol"] for t in tickers}
    sympathy = []
    for s in data.get("sympathy_tickers") or []:
        if isinstance(s, str) and s.strip():
            sym = s.strip().upper()
            if sym not in primary and sym not in sympathy:
                sympathy.append(sym)
    return {
        "score": score,
        "tickers": tickers,
        "sympathy_tickers": sympathy[:3],
        "category": str(data.get("category", "other")),
        "rationale": str(data.get("rationale", ""))[:300],
    }
