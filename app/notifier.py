import httpx

from .config import settings


async def send_alert(item: dict, result: dict, pipeline_seconds: float) -> None:
    tickers = "  ".join(f"{t['symbol']} {t['direction'].upper()}" for t in result["tickers"])
    lines = [
        f"<b>{tickers}</b> — score {result['score']}/100",
        item.get("headline", ""),
        f"<i>{result['rationale']}</i>",
    ]
    if item.get("url"):
        lines.append(item["url"])
    lines.append(
        f"{result['category']} | {item.get('source', '')} | {pipeline_seconds:.1f}s wire-to-alert"
    )
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
