import argparse
import asyncio
import json
import time

from .classifier import classify
from .config import settings
from .filters import Filters
from .notifier import send_alert, send_test
from .store import Store
from .stream import news_stream
from .web import serve_status


async def run() -> None:
    store = Store(settings.db_path)
    filters = Filters(settings)
    web_task = asyncio.create_task(serve_status(settings.port, settings.db_path))
    web_task.add_done_callback(
        lambda t: print(f"status server died: {t.exception()!r}") if t.exception() else None)
    print(
        f"candlechaser starting "
        f"(threshold={settings.alert_score_threshold}, model={settings.anthropic_model})"
    )
    async for item in news_stream():
        received = time.time()
        reason = filters.pre_skip(item)
        if reason:
            # Don't log outside-window skips; overnight wire volume would bloat the DB.
            if reason != "outside_alert_window":
                store.log(item, skip_reason=reason)
            continue

        result = await classify(item)
        if result is None:
            store.log(item, skip_reason="classifier_error")
            continue

        alerted = False
        if result["score"] >= settings.alert_score_threshold:
            symbols = filters.tradeable_symbols([t["symbol"] for t in result["tickers"]])
            if symbols:
                alert_result = {
                    **result,
                    "tickers": [t for t in result["tickers"] if t["symbol"] in symbols],
                }
                try:
                    await send_alert(item, alert_result, time.time() - received)
                    filters.mark_alerted(symbols)
                    alerted = True
                except Exception as exc:
                    print(f"alert send failed: {exc!r}")

        store.log(item, result=result, alerted=alerted)
        flag = "ALERT" if alerted else "     "
        print(f"{flag} [{result['score']:3d}] {item.get('headline', '')[:110]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="candlechaser: realtime headline alerts")
    parser.add_argument("--test-telegram", action="store_true",
                        help="send a test Telegram message and exit")
    parser.add_argument("--classify", metavar="HEADLINE",
                        help="classify a single headline and print the result")
    args = parser.parse_args()

    if args.test_telegram:
        asyncio.run(send_test())
        print("test message sent")
        return
    if args.classify:
        fake = {"headline": args.classify, "summary": "", "symbols": [],
                "source": "manual", "created_at": ""}
        print(json.dumps(asyncio.run(classify(fake)), indent=2))
        return
    asyncio.run(run())


if __name__ == "__main__":
    main()
