import httpx

from .config import settings
from .events import Event


async def send_alert(alert_id: str, event: Event, result: dict,
                     pipeline_seconds: float) -> None:
    # The bracket tag is the journaling key (copy-pasted into happytrader):
    # short, on its own line, always first.
    tag = f"[{alert_id} | {event.source.upper()}:{result['category']}]"
    tickers = "  ".join(f"{t['symbol']} {t['direction'].upper()}" for t in result["tickers"])
    lines = [
        f"<code>{tag}</code>",
        f"<b>{tickers}</b> — score {result['score']}/100",
        event.text,
        f"<i>{result['rationale']}</i>",
    ]
    if event.url:
        lines.append(event.url)
    wire = event.meta.get("wire") or event.source
    lines.append(f"{wire} | {pipeline_seconds:.1f}s wire-to-alert")
    await _send("\n".join(lines))


async def send_test() -> None:
    await _send("candlechaser test message: Telegram wiring works.")


async def _send(text: str) -> None:
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, json={
            "chat_id": settings.telegram_chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        })
        resp.raise_for_status()
