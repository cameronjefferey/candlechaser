"""Measures what actually happened after each classified headline.

Every few minutes, finds headlines past their 60-minute observation window,
pulls 1-min bars from Alpaca's free IEX feed (REST — doesn't count against
the websocket connection limit), and records the max up/down move at 30 and
60 minutes. This is the ground truth used to calibrate scores over time.
"""

import asyncio
import json
from datetime import datetime, timezone

import httpx

from .config import settings
from .store import Store

BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
POLL_SECONDS = 300


async def track_outcomes(store: Store) -> None:
    await asyncio.sleep(60)  # let the worker settle before first poll
    while True:
        try:
            n = await _measure_batch(store)
            if n:
                print(f"tracker: measured {n} headline outcomes")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"tracker: batch failed: {exc!r}")
        await asyncio.sleep(POLL_SECONDS)


async def _measure_batch(store: Store) -> int:
    pending = store.pending_outcomes(limit=100)
    if not pending:
        return 0
    headers = {
        "APCA-API-KEY-ID": settings.alpaca_key_id,
        "APCA-API-SECRET-KEY": settings.alpaca_secret_key,
    }
    measured = 0
    async with httpx.AsyncClient(timeout=20, headers=headers) as client:
        for headline_id, received_at, score, alerted, tickers_json in pending:
            tickers = json.loads(tickers_json)
            symbols = ",".join(t["symbol"] for t in tickers)
            try:
                resp = await client.get(BARS_URL, params={
                    "symbols": symbols,
                    "timeframe": "1Min",
                    "start": _iso(received_at),
                    "end": _iso(received_at + 3600),
                    "feed": "iex",
                    "limit": 10000,
                })
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if 400 <= exc.response.status_code < 500:
                    # Bad symbol (e.g. a non-US suffix the LLM invented): mark it
                    # so it can never block the rest of the queue again.
                    for t in tickers:
                        store.save_outcome(headline_id, t["symbol"], t.get("direction"),
                                           score, alerted, {"status": "error"})
                    print(f"tracker: bars rejected for {symbols!r}, marked error")
                    continue
                raise  # 5xx: transient, retry whole batch next cycle
            bars_by_symbol = resp.json().get("bars") or {}
            for t in tickers:
                outcome = _compute(bars_by_symbol.get(t["symbol"]) or [], received_at)
                store.save_outcome(headline_id, t["symbol"], t.get("direction"),
                                   score, alerted, outcome)
                measured += 1
    return measured


def _compute(bars: list[dict], received_at: float) -> dict:
    """Max percentage move up/down from the first post-headline price, within
    30 and 60 minutes. IEX has thin extended-hours coverage, so overnight
    headlines often come back no_data — that's expected."""
    if not bars:
        return {"status": "no_data"}
    base = bars[0]["o"]
    if not base:
        return {"status": "no_data"}
    out = {"status": "ok", "base_price": base}
    for label, window in (("30m", 1800), ("60m", 3600)):
        in_window = [b for b in bars if _ts(b["t"]) <= received_at + window]
        if in_window:
            out[f"max_up_{label}"] = round((max(b["h"] for b in in_window) - base) / base * 100, 2)
            out[f"max_down_{label}"] = round((min(b["l"] for b in in_window) - base) / base * 100, 2)
    return out


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts(rfc3339: str) -> float:
    return datetime.fromisoformat(rfc3339.replace("Z", "+00:00")).timestamp()
