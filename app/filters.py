import hashlib
import re
import time
from collections import OrderedDict
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


class Filters:
    """Cheap checks that run before spending an LLM call, plus alert cooldowns."""

    def __init__(self, settings):
        self.settings = settings
        self._seen_ids: OrderedDict = OrderedDict()
        self._seen_headlines: OrderedDict = OrderedDict()
        self._cooldowns: dict[str, float] = {}

    def pre_skip(self, item: dict) -> str | None:
        """Return a skip reason, or None if the item should be classified."""
        if not item.get("headline"):
            return "empty_headline"
        if not self._in_alert_window():
            return "outside_alert_window"
        item_id = item.get("id")
        if item_id in self._seen_ids:
            return "duplicate_id"
        self._remember(self._seen_ids, item_id)
        digest = self._headline_digest(item["headline"])
        if digest in self._seen_headlines:
            return "duplicate_headline"
        self._remember(self._seen_headlines, digest)
        return None

    def tradeable_symbols(self, symbols: list[str]) -> list[str]:
        """Drop symbols that alerted within the cooldown window."""
        now = time.time()
        window = self.settings.ticker_cooldown_minutes * 60
        return [s for s in symbols if now - self._cooldowns.get(s, 0) > window]

    def mark_alerted(self, symbols: list[str]) -> None:
        now = time.time()
        for s in symbols:
            self._cooldowns[s] = now

    def _in_alert_window(self) -> bool:
        if not self.settings.market_hours_only:
            return True
        now = datetime.now(ET)
        if now.weekday() >= 5:
            return False
        hhmm = now.strftime("%H:%M")
        return self.settings.alert_start_et <= hhmm < self.settings.alert_end_et

    @staticmethod
    def _headline_digest(headline: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", " ", headline.lower()).strip()
        return hashlib.sha1(normalized.encode()).hexdigest()

    @staticmethod
    def _remember(cache: OrderedDict, key, max_size: int = 5000) -> None:
        cache[key] = time.time()
        while len(cache) > max_size:
            cache.popitem(last=False)
