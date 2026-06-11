import hashlib
import re
import time
from collections import OrderedDict
from datetime import datetime
from zoneinfo import ZoneInfo

from .events import Event

ET = ZoneInfo("America/New_York")


class Filters:
    """Cheap checks that run before spending an LLM call, plus alert cooldowns."""

    def __init__(self, settings):
        self.settings = settings
        self._seen_ids: OrderedDict = OrderedDict()
        self._seen_texts: OrderedDict = OrderedDict()
        self._cooldowns: dict[tuple[str, str], float] = {}

    def pre_skip(self, event: Event) -> str | None:
        """Return a skip reason, or None if the event should be classified."""
        if not event.text:
            return "empty_text"
        if not self._in_alert_window():
            return "outside_alert_window"
        dedupe_id = (event.source, event.source_id)
        if dedupe_id in self._seen_ids:
            return "duplicate_id"
        self._remember(self._seen_ids, dedupe_id)
        digest = self._text_digest(event.text)
        if digest in self._seen_texts:
            return "duplicate_text"
        self._remember(self._seen_texts, digest)
        return None

    def tradeable_symbols(self, source: str, symbols: list[str]) -> list[str]:
        """Drop symbols that alerted within the cooldown window.

        Cooldowns are per (source, ticker): a filing alert and a news alert on
        the same ticker confirm each other — that's information, not spam.
        """
        now = time.time()
        window = self.settings.ticker_cooldown_minutes * 60
        return [s for s in symbols if now - self._cooldowns.get((source, s), 0) > window]

    def mark_alerted(self, source: str, symbols: list[str]) -> None:
        now = time.time()
        for s in symbols:
            self._cooldowns[(source, s)] = now

    def _in_alert_window(self) -> bool:
        if not self.settings.market_hours_only:
            return True
        now = datetime.now(ET)
        if now.weekday() >= 5:
            return False
        hhmm = now.strftime("%H:%M")
        return self.settings.alert_start_et <= hhmm < self.settings.alert_end_et

    @staticmethod
    def _text_digest(text: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
        return hashlib.sha1(normalized.encode()).hexdigest()

    @staticmethod
    def _remember(cache: OrderedDict, key, max_size: int = 5000) -> None:
        cache[key] = time.time()
        while len(cache) > max_size:
            cache.popitem(last=False)
