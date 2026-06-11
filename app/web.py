"""Minimal status page served alongside the worker.

Gives Render's web service a port to health-check, and gives humans a
quick view of what the worker has been doing. Stdlib only.
"""

import asyncio
import html
import json
import sqlite3
import time

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
<p class="sub">% of classified headlines where the stock actually moved &ge;2% within 60 min
(measured from 1-min bars; thin extended-hours data is excluded)</p>
<table>
<tr><th>score bucket</th><th>measured</th><th>hit &ge;2%</th><th>median max move</th></tr>
{calibration_rows}
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


def _calibration_rows(outcomes: list[tuple]) -> str:
    rows = []
    for lo, hi, label in BUCKETS:
        moves = [max(up or 0, -(down or 0))
                 for score, _alerted, up, down in outcomes if lo <= (score or 0) <= hi]
        if not moves:
            rows.append(f"<tr><td>{label}</td><td>0</td><td>—</td><td>—</td></tr>")
            continue
        hits = sum(1 for m in moves if m >= 2)
        moves.sort()
        median = moves[len(moves) // 2]
        rows.append(
            f"<tr><td>{label}</td><td>{len(moves)}</td>"
            f"<td>{hits / len(moves) * 100:.0f}%</td><td>{median:+.1f}%</td></tr>")
    return "\n".join(rows)


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
            """SELECT score, alerted, max_up_60m, max_down_60m
               FROM outcomes WHERE status = 'ok'""").fetchall()
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
                       calibration_rows=_calibration_rows(outcomes),
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
