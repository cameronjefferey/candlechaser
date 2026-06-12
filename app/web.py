"""Minimal status page served alongside the worker.

Gives Render's web service a port to health-check, and gives humans a
quick view of what the worker has been doing. Stdlib only.
"""

import asyncio
import html
import json
import sqlite3
import time

from .config import settings

_started_at = time.time()

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>candlechaser</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ background: #0d1117; color: #e6edf3; font: 15px/1.5 -apple-system, "Segoe UI", sans-serif;
         max-width: 880px; margin: 2rem auto; padding: 0 1rem; }}
  h1 {{ font-size: 1.4rem; }} h1 span {{ color: #3fb950; }}
  .stats {{ display: flex; gap: 2rem; flex-wrap: wrap; margin: 1rem 0 2rem; }}
  .stat b {{ display: block; font-size: 1.6rem; }}
  .stat small {{ color: #8b949e; }}
  table {{ border-collapse: collapse; width: 100%; }}
  td, th {{ padding: .45rem .6rem; border-bottom: 1px solid #21262d; text-align: left;
           vertical-align: top; }}
  th {{ color: #8b949e; font-weight: 600; }}
  .score {{ font-variant-numeric: tabular-nums; font-weight: 700; }}
  .hot {{ color: #3fb950; }} .warm {{ color: #d29922; }} .cold {{ color: #8b949e; }}
  .tick {{ color: #58a6ff; white-space: nowrap; }}
  .alerted {{ background: rgba(63, 185, 80, .08); }}
  h2 {{ font-size: 1.05rem; margin-top: 2.2rem; }}
  h3 {{ font-size: .9rem; color: #8b949e; margin: 1.4rem 0 .4rem; }}
  .sub {{ color: #8b949e; font-size: .85rem; margin-top: -.4rem; }}
</style>
</head>
<body>
<h1>candle<span>chaser</span></h1>
<div class="stats">
  <div class="stat"><b>{uptime}</b><small>uptime</small></div>
  <div class="stat"><b>{seen_24h}</b><small>headlines / 24h</small></div>
  <div class="stat"><b>{scored_24h}</b><small>classified / 24h</small></div>
  <div class="stat"><b>{alerts_24h}</b><small>alerts / 24h</small></div>
</div>
<h2>calibration</h2>
<p class="sub">Direction-aware: a hit means the stock moved &ge;2% within 60 min
<b>in the predicted direction</b> ("unclear" counts either way). Split by source —
halt "outcomes" mostly measure the move that caused the halt, so only news/filing
numbers reflect buyable alerts.</p>
{calibration_sections}

<h2>would-be alerts</h2>
<p class="sub">Headlines at/above the alert threshold with their measured outcome
(halts excluded) — this is the feed being judged before alerts resume.
✓ = moved &ge;2% in the predicted direction within 60 min.</p>
<table>
<tr><th>hit</th><th>score</th><th>move</th><th>tickers</th><th>headline</th></tr>
{wouldbe_rows}
</table>

<h2>recent headlines</h2>
<table>
<tr><th>score</th><th>tickers</th><th>headline</th></tr>
{rows}
</table>
</body>
</html>"""


def _uptime() -> str:
    secs = int(time.time() - _started_at)
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h {(secs % 3600) // 60}m"
    return f"{secs // 86400}d {(secs % 86400) // 3600}h"


def _score_class(score: int) -> str:
    return "hot" if score >= 70 else "warm" if score >= 50 else "cold"


BUCKETS = [(85, 100, "85-100"), (70, 84, "70-84"), (50, 69, "50-69"),
           (30, 49, "30-49"), (0, 29, "0-29")]


def directional_hit(direction: str | None, up: float | None, down: float | None,
                    threshold: float = 2.0) -> bool:
    """Did the stock move >=threshold% in the predicted direction?"""
    up, down = up or 0, down or 0
    if direction == "up":
        return up >= threshold
    if direction == "down":
        return down <= -threshold
    return up >= threshold or down <= -threshold  # unclear: either way counts


def favorable_move(direction: str | None, up: float | None, down: float | None) -> float:
    """The move in the predicted direction (or best move if unclear)."""
    up, down = up or 0, down or 0
    if direction == "up":
        return up
    if direction == "down":
        return -down
    return max(up, -down)


SOURCE_ORDER = ["news", "filing", "halt"]


def _calibration_sections(outcomes: list[tuple]) -> str:
    """One bucket table per source. Rows are (source, score, alerted, direction, up, down)."""
    by_source: dict[str, list[tuple]] = {}
    for source, *rest in outcomes:
        by_source.setdefault(source or "?", []).append(tuple(rest))
    if not by_source:
        return "<p class='sub'>no measured outcomes yet</p>"
    sections = []
    ordered = sorted(by_source, key=lambda s: (SOURCE_ORDER.index(s)
                                               if s in SOURCE_ORDER else 99, s))
    for source in ordered:
        sections.append(
            f"<h3>{html.escape(source)}</h3>\n<table>\n"
            "<tr><th>score bucket</th><th>measured</th><th>hit &ge;2% w/ direction</th>"
            "<th>moved &ge;2% any direction</th><th>median favorable move</th></tr>\n"
            f"{_calibration_rows(by_source[source])}\n</table>")
    return "\n".join(sections)


def _calibration_rows(outcomes: list[tuple]) -> str:
    rows = []
    for lo, hi, label in BUCKETS:
        bucket = [(d, up, down) for score, _a, d, up, down in outcomes
                  if lo <= (score or 0) <= hi]
        if not bucket:
            rows.append(f"<tr><td>{label}</td><td>0</td><td>—</td><td>—</td><td>—</td></tr>")
            continue
        n = len(bucket)
        dir_hits = sum(1 for d, up, down in bucket if directional_hit(d, up, down))
        any_hits = sum(1 for d, up, down in bucket
                       if max(up or 0, -(down or 0)) >= 2)
        moves = sorted(favorable_move(d, up, down) for d, up, down in bucket)
        median = moves[n // 2]
        rows.append(
            f"<tr><td>{label}</td><td>{n}</td>"
            f"<td>{dir_hits / n * 100:.0f}%</td><td>{any_hits / n * 100:.0f}%</td>"
            f"<td>{median:+.1f}%</td></tr>")
    return "\n".join(rows)


def _wouldbe_rows(rows_data: list[tuple]) -> str:
    rows = []
    for headline, score, tickers_json, direction, up, down, status in rows_data:
        tickers = " ".join(
            f"{t['symbol']}{'↑' if t['direction'] == 'up' else '↓' if t['direction'] == 'down' else ''}"
            for t in json.loads(tickers_json or "[]"))
        if status is None:
            hit, move = "…", "pending"
        elif status != "ok":
            hit, move = "?", "no data"
        else:
            hit = "✓" if directional_hit(direction, up, down) else "✗"
            move = f"{favorable_move(direction, up, down):+.1f}%"
        color = "hot" if hit == "✓" else "cold" if hit in ("…", "?") else "warm"
        rows.append(
            f'<tr><td class="{color}">{hit}</td>'
            f'<td class="score {_score_class(score)}">{score}</td>'
            f'<td>{move}</td><td class="tick">{html.escape(tickers)}</td>'
            f'<td>{html.escape(headline or "")}</td></tr>')
    return "\n".join(rows) or '<tr><td colspan="5">nothing at threshold yet</td></tr>'


def _render_page(db_path: str) -> str:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        since = time.time() - 86400
        seen, scored, alerts = conn.execute(
            """SELECT COUNT(*), COUNT(score), SUM(alerted)
               FROM headlines WHERE received_at > ?""", (since,)).fetchone()
        recent = conn.execute(
            """SELECT score, result_tickers, headline, alerted
               FROM headlines WHERE score IS NOT NULL
               ORDER BY received_at DESC LIMIT 30""").fetchall()
        outcomes = conn.execute(
            """SELECT h.source, o.score, o.alerted, o.direction,
                      o.max_up_60m, o.max_down_60m
               FROM outcomes o JOIN headlines h ON h.id = o.headline_id
               WHERE o.status = 'ok'""").fetchall()
        wouldbe = conn.execute(
            """SELECT h.headline, h.score, h.result_tickers,
                      o.direction, o.max_up_60m, o.max_down_60m, o.status
               FROM headlines h
               LEFT JOIN outcomes o ON o.headline_id = h.id
               WHERE h.score >= ? AND h.source != 'halt'
               GROUP BY h.id
               ORDER BY h.received_at DESC LIMIT 25""",
            (settings.alert_score_threshold,)).fetchall()
    finally:
        conn.close()

    rows = []
    for score, tickers_json, headline, alerted in recent:
        tickers = " ".join(
            f"{t['symbol']}{'↑' if t['direction'] == 'up' else '↓' if t['direction'] == 'down' else ''}"
            for t in json.loads(tickers_json or "[]"))
        rows.append(
            f'<tr class="{"alerted" if alerted else ""}">'
            f'<td class="score {_score_class(score)}">{score}</td>'
            f'<td class="tick">{html.escape(tickers)}</td>'
            f'<td>{html.escape(headline or "")}</td></tr>')

    return PAGE.format(uptime=_uptime(), seen_24h=seen or 0, scored_24h=scored or 0,
                       alerts_24h=alerts or 0,
                       calibration_sections=_calibration_sections(outcomes),
                       wouldbe_rows=_wouldbe_rows(wouldbe),
                       rows="\n".join(rows) or '<tr><td colspan="3">nothing scored yet</td></tr>')


async def serve_status(port: int, db_path: str) -> None:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await asyncio.wait_for(reader.readline(), timeout=5)
            try:
                body = _render_page(db_path).encode()
                status = b"200 OK"
            except Exception as exc:
                body = f"status page error: {exc!r}".encode()
                status = b"500 Internal Server Error"
            writer.write(
                b"HTTP/1.1 " + status +
                b"\r\nContent-Type: text/html; charset=utf-8"
                b"\r\nContent-Length: " + str(len(body)).encode() +
                b"\r\nConnection: close\r\n\r\n" + body)
            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "0.0.0.0", port)
    print(f"status page listening on :{port}")
    async with server:
        await server.serve_forever()
