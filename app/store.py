import json
import sqlite3
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from .events import Event

ET = ZoneInfo("America/New_York")

SCHEMA = """
CREATE TABLE IF NOT EXISTS headlines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wire_id TEXT,
    received_at REAL NOT NULL,
    headline TEXT,
    symbols TEXT,
    source TEXT,
    url TEXT,
    skip_reason TEXT,
    score INTEGER,
    category TEXT,
    rationale TEXT,
    result_tickers TEXT,
    alerted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_headlines_received ON headlines (received_at);

CREATE TABLE IF NOT EXISTS outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    headline_id INTEGER NOT NULL REFERENCES headlines (id),
    symbol TEXT NOT NULL,
    direction TEXT,
    score INTEGER,
    alerted INTEGER,
    base_price REAL,
    max_up_30m REAL,
    max_down_30m REAL,
    max_up_60m REAL,
    max_down_60m REAL,
    status TEXT NOT NULL,
    measured_at REAL NOT NULL,
    UNIQUE (headline_id, symbol)
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id TEXT PRIMARY KEY,        -- CC-20260611-007
    created_at REAL NOT NULL,
    source TEXT NOT NULL,             -- news | filing | halt | options
    subtype TEXT,                     -- e.g. 8-K, LUDP, cluster_buy, exec_comment
    tickers TEXT NOT NULL,            -- JSON: [{"symbol","direction"}]
    score INTEGER,
    headline TEXT,
    url TEXT,
    sympathy TEXT                     -- JSON list, filled in Phase 1
);
"""


class Store:
    """Logs every headline and classification so thresholds can be tuned later."""

    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def log(self, event: Event, skip_reason: str | None = None,
            result: dict | None = None, alerted: bool = False) -> None:
        result = result or {}
        self.conn.execute(
            """INSERT INTO headlines (wire_id, received_at, headline, symbols, source, url,
               skip_reason, score, category, rationale, result_tickers, alerted)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.source_id,
                event.ts,
                event.text,
                json.dumps(event.symbols),
                event.source,
                event.url,
                skip_reason,
                result.get("score"),
                result.get("category"),
                result.get("rationale"),
                json.dumps(result.get("tickers") or []),
                int(alerted),
            ),
        )
        self.conn.commit()

    def create_alert(self, source: str, subtype: str | None, tickers: list[dict],
                     score: int | None, headline: str, url: str,
                     sympathy: list[str] | None = None) -> str:
        """Mint the next CC-YYYYMMDD-NNN id (restart-safe: derived from the
        table) and persist the alert."""
        day = datetime.now(ET).strftime("%Y%m%d")
        prefix = f"CC-{day}-"
        row = self.conn.execute(
            "SELECT alert_id FROM alerts WHERE alert_id LIKE ? ORDER BY alert_id DESC LIMIT 1",
            (prefix + "%",)).fetchone()
        seq = int(row[0].rsplit("-", 1)[1]) + 1 if row else 1
        alert_id = f"{prefix}{seq:03d}"
        self.conn.execute(
            """INSERT INTO alerts (alert_id, created_at, source, subtype, tickers,
               score, headline, url, sympathy)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (alert_id, time.time(), source, subtype, json.dumps(tickers),
             score, headline, url, json.dumps(sympathy or [])),
        )
        self.conn.commit()
        return alert_id

    def export_alerts(self, since_ts: float = 0) -> list[tuple]:
        """One row per ticker per alert, for the happytrader journaling CSV."""
        rows = []
        for alert_id, created_at, source, subtype, tickers, score, headline, url in \
                self.conn.execute(
                    """SELECT alert_id, created_at, source, subtype, tickers, score,
                       headline, url FROM alerts WHERE created_at >= ?
                       ORDER BY created_at""", (since_ts,)):
            created_iso = datetime.fromtimestamp(created_at, tz=ET).isoformat(timespec="seconds")
            for t in json.loads(tickers):
                rows.append((alert_id, created_iso, source, subtype,
                             t.get("symbol"), t.get("direction"), score, headline, url))
        return rows

    def pending_outcomes(self, min_age_secs: int = 3900, max_age_secs: int = 259200,
                         limit: int = 25) -> list[tuple]:
        """Classified headlines old enough to measure (60m window + buffer) that
        have tickers and no outcome rows yet."""
        now = time.time()
        return self.conn.execute(
            """SELECT id, received_at, score, alerted, result_tickers
               FROM headlines
               WHERE score IS NOT NULL
                 AND result_tickers != '[]'
                 AND received_at < ? AND received_at > ?
                 AND id NOT IN (SELECT headline_id FROM outcomes)
               ORDER BY received_at
               LIMIT ?""",
            (now - min_age_secs, now - max_age_secs, limit),
        ).fetchall()

    def save_outcome(self, headline_id: int, symbol: str, direction: str | None,
                     score: int, alerted: int, outcome: dict) -> None:
        self.conn.execute(
            """INSERT OR IGNORE INTO outcomes
               (headline_id, symbol, direction, score, alerted, base_price,
                max_up_30m, max_down_30m, max_up_60m, max_down_60m, status, measured_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                headline_id, symbol, direction, score, alerted,
                outcome.get("base_price"),
                outcome.get("max_up_30m"), outcome.get("max_down_30m"),
                outcome.get("max_up_60m"), outcome.get("max_down_60m"),
                outcome["status"], time.time(),
            ),
        )
        self.conn.commit()

    def measured_outcomes(self) -> list[tuple]:
        """(score, alerted, max_up_60m, max_down_60m) for all measured outcomes."""
        return self.conn.execute(
            """SELECT score, alerted, max_up_60m, max_down_60m
               FROM outcomes WHERE status = 'ok'""").fetchall()
