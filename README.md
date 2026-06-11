# candlechaser

Realtime headline alerts for intraday trading. Streams the Benzinga news wire via
Alpaca's free websocket, scores each headline with an LLM for "will this move a stock
≥2% intraday", and pushes Telegram alerts within seconds.

## Setup

1. Copy `.env.example` to `.env` and fill in your keys (see comments in the file).
2. Install and run:

   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt

3. Verify the plumbing:

   python -m app.main --test-telegram
   python -m app.main --classify "NVDA CEO says Marvell will be the next trillion dollar company"

4. Run the worker:

   python -m app.main

Every headline is logged to SQLite (`candlechaser.db`) with its score, whether it alerted,
and the classifier's rationale — use it to tune `ALERT_SCORE_THRESHOLD`.

## Alert tags and journaling

Every Telegram alert starts with a bracket tag on its own line:

    [CC-20260611-007 | NEWS:exec_comment]

`CC-YYYYMMDD-NNN` is the alert ID (per-day sequence, restart-safe). Copy the tag into
the happytrader journal entry when you take a trade, then reconcile weekly:

    python -m app.main --export-alerts --since 2026-06-01

writes `alerts.csv` (one row per ticker per alert):
`alert_id, created_at_iso, source, subtype, symbol, direction, score, headline, url`.

### Sources and subtypes

| Source | Subtypes | Status |
|---|---|---|
| `NEWS` | classifier category (`m&a`, `guidance`, `exec_comment`, ...) | live |
| `FILING` | `8-K`, `activist_stake`, `cluster_buy`, `offering` | planned (Phase 2) |
| `HALT` | halt code (`LUDP`, `T1`, ...), `resume` | planned (Phase 3) |
| `OPTIONS` | `sweep` | planned (Phase 4) |

## Tuning

- Start with `ALERT_SCORE_THRESHOLD=70`. After a few days, query the DB: if you're
  getting spammed, raise it; if you're missing movers, lower it and tighten the prompt.
- `MARKET_HOURS_ONLY=true` limits alerts to 07:00–16:00 ET weekdays (premarket included).
  Set `false` to also catch after-hours headlines.
- Per-ticker cooldown (default 15 min) stops repeat alerts on follow-up coverage.

## Deploy

`render.yaml` defines a Render background worker (~$7/mo) with a persistent disk for
the SQLite log. Set the secret env vars in the Render dashboard.
