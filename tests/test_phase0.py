import json
import time
from datetime import datetime
from pathlib import Path

from app.events import Event
from app.sources.news import _to_event
from app.store import ET, Store

FIXTURES = Path(__file__).parent / "fixtures"


def _make_event(**overrides) -> Event:
    defaults = dict(source="news", source_id="1", ts=time.time(),
                    text="Test headline", symbols=["AAPL"], url="https://x.test")
    return Event(**{**defaults, **overrides})


def test_news_message_to_event():
    msg = json.loads((FIXTURES / "news_message.json").read_text())
    event = _to_event(msg)
    assert event.source == "news"
    assert event.source_id == "38123456"
    assert event.text.startswith("NVDA CEO Says Marvell")
    assert event.symbols == ["NVDA", "MRVL"]
    assert event.meta["wire"] == "benzinga"
    assert event.meta["summary"].startswith("Speaking at")


def test_alert_id_sequence_and_format(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    a1 = store.create_alert("news", "exec_comment",
                            [{"symbol": "MRVL", "direction": "up"}], 84, "h1", "u1")
    a2 = store.create_alert("news", "m&a",
                            [{"symbol": "XYZ", "direction": "up"}], 91, "h2", "u2")
    day = datetime.now(ET).strftime("%Y%m%d")
    assert a1 == f"CC-{day}-001"
    assert a2 == f"CC-{day}-002"


def test_alert_id_sequence_survives_restart(tmp_path):
    path = str(tmp_path / "t.db")
    Store(path).create_alert("news", "guidance",
                             [{"symbol": "AAPL", "direction": "down"}], 80, "h", "u")
    fresh = Store(path)  # simulates a worker restart
    a2 = fresh.create_alert("halt", "LUDP",
                            [{"symbol": "GME", "direction": "unclear"}], 80, "h2", "u2")
    assert a2.endswith("-002")


def test_export_one_row_per_ticker(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    store.create_alert("news", "exec_comment",
                       [{"symbol": "MRVL", "direction": "up"},
                        {"symbol": "AVGO", "direction": "up"}],
                       84, "Some headline", "https://x.test")
    rows = store.export_alerts()
    assert len(rows) == 2
    alert_id, created_iso, source, subtype, symbol, direction, score, headline, url = rows[0]
    assert alert_id.startswith("CC-")
    assert (source, subtype, symbol, direction, score) == \
        ("news", "exec_comment", "MRVL", "up", 84)
    assert rows[1][4] == "AVGO"
    datetime.fromisoformat(created_iso)  # valid ISO timestamp


def test_export_since_filter(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    store.create_alert("news", "other", [{"symbol": "A", "direction": "up"}], 70, "h", "u")
    assert store.export_alerts(since_ts=time.time() + 60) == []
    assert len(store.export_alerts(since_ts=0)) == 1


def test_event_log_roundtrip(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    store.log(_make_event(), result={"score": 55, "category": "product",
                                     "rationale": "r",
                                     "tickers": [{"symbol": "AAPL", "direction": "up"}]})
    row = store.conn.execute(
        "SELECT wire_id, headline, source, score FROM headlines").fetchone()
    assert row == ("1", "Test headline", "news", 55)


def test_cooldown_is_per_source(tmp_path):
    from app.config import Settings
    from app.filters import Filters
    settings = Settings(alpaca_key_id="t", alpaca_secret_key="t", anthropic_api_key="t",
                        telegram_bot_token="t", telegram_chat_id="t")
    f = Filters(settings)
    f.mark_alerted("news", ["MRVL"])
    assert f.tradeable_symbols("news", ["MRVL"]) == []
    # A filing alert on the same ticker is confirmation, not spam.
    assert f.tradeable_symbols("filing", ["MRVL"]) == ["MRVL"]
