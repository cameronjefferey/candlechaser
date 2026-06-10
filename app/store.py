import json
import sqlite3
import time

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
"""


class Store:
    """Logs every headline and classification so thresholds can be tuned later."""

    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def log(self, item: dict, skip_reason: str | None = None,
            result: dict | None = None, alerted: bool = False) -> None:
        result = result or {}
        self.conn.execute(
            """INSERT INTO headlines (wire_id, received_at, headline, symbols, source, url,
               skip_reason, score, category, rationale, result_tickers, alerted)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(item.get("id", "")),
                time.time(),
                item.get("headline"),
                json.dumps(item.get("symbols") or []),
                item.get("source"),
                item.get("url"),
                skip_reason,
                result.get("score"),
                result.get("category"),
                result.get("rationale"),
                json.dumps(result.get("tickers") or []),
                int(alerted),
            ),
        )
        self.conn.commit()
