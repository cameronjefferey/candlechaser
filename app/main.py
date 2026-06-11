import argparse
import asyncio
import csv
import json
import time
from datetime import datetime, timezone

from .classifier import classify
from .config import settings
from .events import Event
from .filters import Filters
from .notifier import send_alert, send_test
from .sources import news
from .store import Store
from .tracker import track_outcomes
from .web import serve_status


def _watch(task: asyncio.Task, name: str) -> None:
    task.add_done_callback(
        lambda t: print(f"{name} died: {t.exception()!r}") if t.exception() else None)


def _enabled_sources() -> dict:
    sources = {}
    if settings.enable_news:
        sources["news"] = news.stream
    # Later phases: filings, halts, options.
    return sources


async def _pump(name: str, source, queue: asyncio.Queue) -> None:
    """Source isolation: one source crashing must not kill the worker."""
    backoff = 1
    while True:
        try:
            async for event in source():
                await queue.put(event)
            backoff = 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"{name}: source crashed ({exc!r}); restarting in {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300)


async def _handle(event: Event, store: Store, filters: Filters) -> None:
    reason = filters.pre_skip(event)
    if reason:
        # Don't log outside-window skips; overnight wire volume would bloat the DB.
        if reason != "outside_alert_window":
            store.log(event, skip_reason=reason)
        return

    result = await classify(event)
    if result is None:
        store.log(event, skip_reason="classifier_error")
        return

    alerted = False
    if result["score"] >= settings.alert_score_threshold:
        symbols = filters.tradeable_symbols(
            event.source, [t["symbol"] for t in result["tickers"]])
        if symbols:
            alert_tickers = [t for t in result["tickers"] if t["symbol"] in symbols]
            alert_id = store.create_alert(
                source=event.source, subtype=result["category"],
                tickers=alert_tickers, score=result["score"],
                headline=event.text, url=event.url)
            alert_result = {**result, "tickers": alert_tickers}
            try:
                await send_alert(alert_id, event, alert_result, time.time() - event.ts)
                filters.mark_alerted(event.source, symbols)
                alerted = True
            except Exception as exc:
                print(f"alert send failed for {alert_id}: {exc!r}")

    store.log(event, result=result, alerted=alerted)
    flag = "ALERT" if alerted else "     "
    print(f"{flag} [{result['score']:3d}] ({event.source}) {event.text[:100]}")


async def run() -> None:
    store = Store(settings.db_path)
    filters = Filters(settings)
    queue: asyncio.Queue[Event] = asyncio.Queue()
    _watch(asyncio.create_task(serve_status(settings.port, settings.db_path)), "status server")
    _watch(asyncio.create_task(track_outcomes(store)), "outcome tracker")
    sources = _enabled_sources()
    for name, source in sources.items():
        _watch(asyncio.create_task(_pump(name, source, queue)), f"source:{name}")
    print(
        f"candlechaser starting "
        f"(sources={'+'.join(sources) or 'none'}, "
        f"threshold={settings.alert_score_threshold}, model={settings.anthropic_model})"
    )
    while True:
        event = await queue.get()
        try:
            await _handle(event, store, filters)
        except Exception as exc:
            print(f"pipeline error on {event.source}:{event.source_id}: {exc!r}")


def export_alerts(since: str | None) -> None:
    since_ts = 0.0
    if since:
        since_ts = datetime.strptime(since, "%Y-%m-%d").replace(
            tzinfo=timezone.utc).timestamp()
    store = Store(settings.db_path)
    rows = store.export_alerts(since_ts)
    with open("alerts.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["alert_id", "created_at_iso", "source", "subtype",
                         "symbol", "direction", "score", "headline", "url"])
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows to alerts.csv")


def main() -> None:
    parser = argparse.ArgumentParser(description="candlechaser: realtime headline alerts")
    parser.add_argument("--test-telegram", action="store_true",
                        help="send a test Telegram message and exit")
    parser.add_argument("--classify", metavar="HEADLINE",
                        help="classify a single headline and print the result")
    parser.add_argument("--export-alerts", action="store_true",
                        help="export alerts to alerts.csv for journaling")
    parser.add_argument("--since", metavar="YYYY-MM-DD",
                        help="with --export-alerts: only alerts on/after this date")
    args = parser.parse_args()

    if args.test_telegram:
        asyncio.run(send_test())
        print("test message sent")
        return
    if args.classify:
        fake = Event(source="news", source_id="manual", ts=time.time(),
                     text=args.classify)
        print(json.dumps(asyncio.run(classify(fake)), indent=2))
        return
    if args.export_alerts:
        export_alerts(args.since)
        return
    asyncio.run(run())


if __name__ == "__main__":
    main()
