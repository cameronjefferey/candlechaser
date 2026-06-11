"""Nasdaq trade-halt source (LULD pauses, T1 news pending, etc).

Pure rules, no LLM. New halt -> immediate alert. When the feed publishes a
resumption time -> a second alert ~1 minute before trading resumes,
referencing the original halt alert. Halts on the first poll are baseline
(already in progress when we started) and never alert.
"""

import asyncio
import time
import xml.etree.ElementTree as ElementTree
from collections.abc import AsyncIterator
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from ..config import settings
from ..events import Event

RSS_URL = "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts"
# nasdaqtrader.com sits behind bot protection that rejects non-browser agents.
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
}
NS = {"ndaq": "http://www.nasdaqtrader.com/"}
ET_TZ = ZoneInfo("America/New_York")
RESUME_LEAD_SECS = 60


def parse_rss(text: str) -> list[dict]:
    root = ElementTree.fromstring(text)
    halts = []
    for item in root.iter("item"):
        get = lambda tag: (item.findtext(f"ndaq:{tag}", "", NS) or "").strip()
        halt = {
            "symbol": get("IssueSymbol"),
            "name": get("IssueName"),
            "market": get("Market"),
            "reason": get("ReasonCode"),
            "halt_date": get("HaltDate"),
            "halt_time": get("HaltTime"),
            "resume_date": get("ResumptionDate"),
            "resume_trade_time": get("ResumptionTradeTime"),
        }
        if halt["symbol"] and halt["reason"]:
            halts.append(halt)
    return halts


def _halt_key(halt: dict) -> tuple:
    return (halt["symbol"], halt["halt_date"], halt["halt_time"])


def _et_timestamp(date_str: str, time_str: str) -> float | None:
    try:
        dt = datetime.strptime(f"{date_str} {time_str}", "%m/%d/%Y %H:%M:%S.%f")
        return dt.replace(tzinfo=ET_TZ).timestamp()
    except ValueError:
        return None


class HaltsTracker:
    """Stateful core, separated from the poll loop so it can be replay-tested."""

    def __init__(self):
        self._seen: dict[tuple, dict] = {}
        self._resume_sent: set[tuple] = set()
        self._baselined = False

    def poll(self, halts: list[dict], now: float | None = None) -> list[Event]:
        now = now or time.time()
        events: list[Event] = []
        for halt in halts:
            key = _halt_key(halt)
            is_new = key not in self._seen
            self._seen[key] = halt
            if self._baselined and is_new:
                events.append(self._halt_event(halt, now))
            elif not self._baselined:
                # Pre-existing halt: never alert it, including its resume.
                self._resume_sent.add(key)
            resume_event = self._maybe_resume(key, halt, now)
            if resume_event:
                events.append(resume_event)
        self._baselined = True
        return events

    def _maybe_resume(self, key: tuple, halt: dict, now: float) -> Event | None:
        if key in self._resume_sent or not halt["resume_trade_time"]:
            return None
        resume_at = _et_timestamp(halt["resume_date"] or halt["halt_date"],
                                  halt["resume_trade_time"])
        if resume_at is None or now < resume_at - RESUME_LEAD_SECS:
            return None
        self._resume_sent.add(key)
        return Event(
            source="halt",
            source_id=f"{halt['symbol']}-{halt['halt_date']}-{halt['halt_time']}-resume",
            ts=now,
            text=(f"Trading resumes {halt['symbol']} ({halt['name']}) at "
                  f"{halt['resume_trade_time'][:8]} ET (halted {halt['reason']} "
                  f"at {halt['halt_time'][:8]})"),
            symbols=[halt["symbol"]],
            meta={
                "subtype": "resume",
                "bypass_cooldown": True,
                "reference_prior": {"source": "halt", "label": "Resume for"},
                "prescored": {
                    "score": settings.halt_score,
                    "tickers": [{"symbol": halt["symbol"], "direction": "unclear"}],
                    "category": "resume",
                    "rationale": "Get set before the first prints after the halt.",
                },
            },
        )

    def _halt_event(self, halt: dict, now: float) -> Event:
        return Event(
            source="halt",
            source_id=f"{halt['symbol']}-{halt['halt_date']}-{halt['halt_time']}",
            ts=now,
            text=(f"Trading halted: {halt['symbol']} ({halt['name']}) — "
                  f"code {halt['reason']} at {halt['halt_time'][:8]} ET "
                  f"on {halt['market']}"),
            symbols=[halt["symbol"]],
            meta={
                "subtype": halt["reason"],
                "bypass_cooldown": True,      # a halt IS the confirmation
                "check_confirmation": True,   # earlier alert on this ticker? CONFIRMED:
                "prescored": {
                    "score": settings.halt_score,
                    "tickers": [{"symbol": halt["symbol"], "direction": "unclear"}],
                    "category": halt["reason"],
                    "rationale": f"{halt['reason']} trading halt — volatility or pending news.",
                },
            },
        )


async def stream() -> AsyncIterator[Event]:
    tracker = HaltsTracker()
    async with httpx.AsyncClient(timeout=15, headers=BROWSER_HEADERS,
                                 follow_redirects=True) as client:
        print("halts: polling started")
        while True:
            resp = None
            try:
                resp = await client.get(RSS_URL)
                resp.raise_for_status()
                for event in tracker.poll(parse_rss(resp.text)):
                    yield event
            except asyncio.CancelledError:
                raise
            except ElementTree.ParseError as exc:
                snippet = (resp.text[:150].replace("\n", " ") if resp else "")
                print(f"halts: unparseable response ({exc!r}); got: {snippet!r}")
            except Exception as exc:
                print(f"halts: poll failed ({exc!r})")
            await asyncio.sleep(settings.halts_poll_seconds)
