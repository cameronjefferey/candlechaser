import asyncio
import json

import websockets

from .config import settings

WS_URL = "wss://stream.data.alpaca.markets/v1beta1/news"


async def news_stream():
    """Yield news items forever, reconnecting with backoff on any failure."""
    backoff = 1
    while True:
        try:
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20) as ws:
                await _handshake(ws)
                print("connected to news stream, subscribed to all symbols")
                backoff = 1
                async for raw in ws:
                    for msg in json.loads(raw):
                        kind = msg.get("T")
                        if kind == "n":
                            yield msg
                        elif kind == "error":
                            print(f"stream error message: {msg}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"stream disconnected ({exc!r}); reconnecting in {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


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
