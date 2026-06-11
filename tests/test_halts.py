from pathlib import Path

from app.sources.halts import HaltsTracker, _et_timestamp, parse_rss

FIXTURES = Path(__file__).parent / "fixtures"


def _new_halt(resumed: bool = False) -> dict:
    return {
        "symbol": "TEST", "name": "Test Corp", "market": "NASDAQ",
        "reason": "LUDP", "halt_date": "06/11/2026", "halt_time": "10:00:00.000",
        "resume_date": "06/11/2026" if resumed else "",
        "resume_trade_time": "10:10:00.000" if resumed else "",
    }


def test_parse_real_rss_snapshot():
    halts = parse_rss((FIXTURES / "halts.rss").read_text())
    assert len(halts) > 0
    kalv = next(h for h in halts if h["symbol"] == "KALV")
    assert kalv["reason"] == "T12"
    assert kalv["halt_time"].startswith("19:50")
    assert kalv["resume_trade_time"] == ""


def test_baseline_halts_never_alert():
    tracker = HaltsTracker()
    halts = parse_rss((FIXTURES / "halts.rss").read_text())
    assert tracker.poll(halts) == []          # first poll = baseline
    assert tracker.poll(halts) == []          # unchanged feed stays quiet


def test_new_halt_fires_once_with_correct_shape():
    tracker = HaltsTracker()
    baseline = parse_rss((FIXTURES / "halts.rss").read_text())
    tracker.poll(baseline)

    events = tracker.poll(baseline + [_new_halt()])
    assert len(events) == 1
    e = events[0]
    assert e.source == "halt"
    assert e.symbols == ["TEST"]
    assert e.meta["subtype"] == "LUDP"
    assert e.meta["bypass_cooldown"] is True
    assert e.meta["prescored"]["score"] == 80
    assert e.meta["prescored"]["tickers"][0]["direction"] == "unclear"
    # Repeat polls don't re-fire.
    assert tracker.poll(baseline + [_new_halt()]) == []


def test_resume_fires_once_inside_lead_window():
    tracker = HaltsTracker()
    tracker.poll([])                          # baseline (empty feed)
    tracker.poll([_new_halt()])               # halt fires

    resume_at = _et_timestamp("06/11/2026", "10:10:00.000")
    # Too early: resumption posted but >60s out.
    assert tracker.poll([_new_halt(resumed=True)], now=resume_at - 300) == []
    # Inside the 60s lead window: resume fires.
    events = tracker.poll([_new_halt(resumed=True)], now=resume_at - 30)
    assert len(events) == 1
    assert events[0].meta["subtype"] == "resume"
    assert events[0].meta["reference_prior"]["source"] == "halt"
    # Never twice.
    assert tracker.poll([_new_halt(resumed=True)], now=resume_at + 60) == []


def test_baseline_halt_resume_never_alerts():
    tracker = HaltsTracker()
    tracker.poll([_new_halt()])               # baseline includes the halt
    resume_at = _et_timestamp("06/11/2026", "10:10:00.000")
    assert tracker.poll([_new_halt(resumed=True)], now=resume_at) == []
