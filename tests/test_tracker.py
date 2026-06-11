import asyncio
import json
import time

import httpx

import app.tracker as tracker
from app.store import Store


class _StubClient:
    """Returns 400 for BADSYM, real-shaped bars for everything else."""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params):
        request = httpx.Request("GET", url)
        if "BADSYM" in params["symbols"]:
            return httpx.Response(400, request=request)
        bars = {s: [{"t": "2026-06-11T14:00:00Z", "o": 100.0, "h": 103.0,
                     "l": 99.0, "c": 102.0}]
                for s in params["symbols"].split(",")}
        return httpx.Response(200, request=request, json={"bars": bars})


def _insert_headline(store, hid_text, tickers, received_at):
    store.conn.execute(
        """INSERT INTO headlines (wire_id, received_at, headline, symbols, source,
           url, score, category, rationale, result_tickers, alerted)
           VALUES (?, ?, ?, '[]', 'news', '', 75, 'other', 'r', ?, 0)""",
        (hid_text, received_at, hid_text, json.dumps(tickers)))
    store.conn.commit()


def test_bad_symbol_does_not_block_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(tracker.httpx, "AsyncClient", _StubClient)
    store = Store(str(tmp_path / "t.db"))
    old = time.time() - 7200
    # Oldest row has the poison symbol; a healthy row sits behind it.
    _insert_headline(store, "poison", [{"symbol": "BADSYM", "direction": "up"}], old)
    _insert_headline(store, "healthy", [{"symbol": "AAPL", "direction": "up"}], old + 60)

    measured = asyncio.run(tracker._measure_batch(store))
    assert measured == 1  # AAPL measured despite BADSYM failing first

    rows = dict(store.conn.execute("SELECT symbol, status FROM outcomes").fetchall())
    assert rows == {"BADSYM": "error", "AAPL": "ok"}
    # Nothing left pending: the poison row never comes back.
    assert store.pending_outcomes() == []
