"""Alpaca news websocket source (Benzinga wire)."""

import asyncio
import json
import time
from collections.abc import AsyncIterator

import websockets

from ..config import settings
from ..events import Event

WS_URL = "wss://stream.data.alpaca.markets/v1beta1/news"


async def stream() -> AsyncIterator[Event]:
    """Yield news Events forever, reconnecting with backoff on any failure."""
    backoff = 1
    while True:
        try:
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20) as ws:
                await _handshake(ws)
                print("news: connected, subscribed to all symbols")
                backoff = 1
                async for raw in ws:
                    for msg in json.loads(raw):
                        kind = msg.get("T")
                        if kind == "n":
                            yield _to_event(msg)
                        elif kind == "error":
                            print(f"news: stream error message: {msg}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"news: disconnected ({exc!r}); reconnecting in {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


def _to_event(msg: dict) -> Event:
    return Event(
        source="news",
        source_id=str(msg.get("id", "")),
        ts=time.time(),
        text=msg.get("headline") or "",
        symbols=msg.get("symbols") or [],
        url=msg.get("url") or "",
        meta={
            "summary": msg.get("summary") or "",
            "wire": msg.get("source") or "",
            "created_at": msg.get("created_at") or "",
        },
    )


async def _handshake(ws):
    await ws.recv()  # [{"T":"success","msg":"connected"}]
    await ws.send(json.dumps({
        "action": "auth",
        "key": settings.alpaca_key_id,
        "secret": settings.alpaca_secret_key,
    }))
    reply = json.loads(await ws.recv())
    if not any(m.get("msg") == "authenticated" for m in reply):
        raise RuntimeError(f"alpaca auth failed: {reply}")
    await ws.send(json.dumps({"action": "subscribe", "news": ["*"]}))
