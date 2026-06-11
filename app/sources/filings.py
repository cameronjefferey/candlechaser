"""SEC EDGAR filings source — the news before the news.

Polls the current-filings Atom feed and handles only high-signal forms:

  8-K            -> fetch doc, extract item numbers, classify with FILING_PROMPT
  SC 13D / 13D/A -> activist stake: direct alert, no LLM
  Form 4         -> open-market buys (code P); 2+ distinct insiders in the
                    window -> cluster_buy alert; single buys logged only
  S-1 / 424B5    -> dilution/offering: direct alert, direction down

Everything else is ignored entirely (volume is huge). SEC asks for a real
contact in the User-Agent and <10 req/s; we poll every 10s and trickle
document fetches.
"""

import asyncio
import json
import re
import time
import xml.etree.ElementTree as ElementTree
from collections import OrderedDict
from collections.abc import AsyncIterator
from pathlib import Path

import httpx

from ..config import settings
from ..events import Event
from ..store import Store

ATOM_URL = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent"
            "&type=&company=&dateb=&owner=include&count=100&output=atom")
TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}

HANDLED_FORMS = {"8-K", "SC 13D", "SC 13D/A", "4", "S-1", "424B5"}
_TITLE_RE = re.compile(r"^(?P<form>.+?) - (?P<name>.+?) \((?P<cik>\d{10})\) \((?P<role>[^)]+)\)$")
_ITEM_RE = re.compile(r"Item[\s\xa0]+(\d+\.\d+)", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


async def stream(store: Store) -> AsyncIterator[Event]:
    headers = {"User-Agent": settings.sec_user_agent}
    tickers = _TickerMap()
    seen: OrderedDict = OrderedDict()
    async with httpx.AsyncClient(timeout=20, headers=headers) as client:
        await tickers.refresh(client)
        print("filings: EDGAR polling started")
        while True:
            try:
                await tickers.refresh_if_stale(client)
                resp = await client.get(ATOM_URL)
                resp.raise_for_status()
                for filing in _parse_atom(resp.text):
                    if filing["accession"] in seen:
                        continue
                    _remember(seen, filing["accession"])
                    try:
                        event = await _process(client, store, tickers, filing)
                    except Exception as exc:
                        print(f"filings: failed on {filing['accession']}: {exc!r}")
                        continue
                    if event:
                        yield event
                    await asyncio.sleep(0.3)  # stay far under SEC rate limits
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"filings: poll failed ({exc!r})")
            await asyncio.sleep(settings.edgar_poll_seconds)


def _parse_atom(text: str) -> list[dict]:
    """Group feed entries by accession; the same filing appears once per role
    (Filer/Issuer/Subject/Reporting)."""
    root = ElementTree.fromstring(text)
    by_accession: dict[str, dict] = {}
    for entry in root.findall("a:entry", ATOM_NS):
        title = entry.findtext("a:title", "", ATOM_NS)
        m = _TITLE_RE.match(title.strip())
        if not m:
            continue
        accession = entry.findtext("a:id", "", ATOM_NS).rsplit("=", 1)[-1]
        link = entry.find("a:link", ATOM_NS)
        f = by_accession.setdefault(accession, {
            "accession": accession,
            "form": m.group("form").strip(),
            "url": link.get("href", "") if link is not None else "",
            "roles": {},
        })
        f["roles"][m.group("role")] = {"cik": m.group("cik"), "name": m.group("name")}
    return [f for f in by_accession.values() if f["form"] in HANDLED_FORMS]


def _company(filing: dict) -> dict | None:
    """The company the filing is about (not the insider/activist filing it)."""
    for role in ("Issuer", "Subject", "Filer", "Filed by"):
        if role in filing["roles"]:
            return filing["roles"][role]
    return None


async def _process(client: httpx.AsyncClient, store: Store, tickers: "_TickerMap",
                   filing: dict) -> Event | None:
    form = filing["form"]
    if form == "4":
        return await _process_form4(client, store, filing)

    company = _company(filing)
    ticker = tickers.lookup(company["cik"]) if company else None
    if not ticker:
        return None
    name = company["name"]

    if form in ("SC 13D", "SC 13D/A"):
        return _prescored_event(
            filing, subtype="activist_stake", score=settings.activist_score,
            direction="up", ticker=ticker,
            text=f"{form}: activist stake disclosed in {name} ({ticker})",
            rationale="Schedule 13D filed — a holder crossed 5% with intent to influence.")

    if form in ("S-1", "424B5"):
        return _prescored_event(
            filing, subtype="offering", score=settings.offering_score,
            direction="down", ticker=ticker,
            text=f"{form}: {name} ({ticker}) registers a share offering",
            rationale="Registration for a share sale — dilution risk.")

    # 8-K: pull the document, extract item numbers, hand to the LLM.
    doc_text = await _fetch_primary_doc(client, filing)
    items = sorted(set(_ITEM_RE.findall(doc_text)))
    items_str = ", ".join(items) if items else "unknown"
    return Event(
        source="filing",
        source_id=filing["accession"],
        ts=time.time(),
        text=f"8-K: {name} ({ticker}) — Items {items_str}",
        symbols=[ticker],
        url=filing["url"],
        meta={"prompt": "filing", "subtype": "8-K", "items": items,
              "summary": doc_text[:1500], "check_confirmation": True},
    )


async def _process_form4(client: httpx.AsyncClient, store: Store,
                         filing: dict) -> Event | None:
    xml_text = await _fetch_form4_xml(client, filing)
    if not xml_text:
        return None
    parsed = parse_form4(xml_text)
    if not parsed or not parsed["open_market_buy"]:
        return None  # sells, option exercises, grants: ignore entirely

    ticker, insider = parsed["ticker"], parsed["insider"]
    store.record_insider_buy(filing["accession"], ticker, insider)
    window_secs = settings.cluster_buy_window_days * 7 / 5 * 86400  # trading->calendar days
    buyers = store.insider_buyers(ticker, window_secs)
    if len(buyers) < settings.cluster_buy_min_insiders:
        return None  # single buys: logged in insider_buys, no alert

    return _prescored_event(
        filing, subtype="cluster_buy", score=settings.cluster_buy_score,
        direction="up", ticker=ticker,
        text=(f"Form 4 cluster: {len(buyers)} insiders bought {ticker} on the open market "
              f"within {settings.cluster_buy_window_days} trading days "
              f"({', '.join(sorted(buyers)[:4])})"),
        rationale="Multiple insiders buying with their own money is a strong signal.")


def parse_form4(xml_text: str) -> dict | None:
    """Extract issuer ticker, insider name, and whether any transaction is an
    open-market purchase (code P)."""
    root = ElementTree.fromstring(xml_text)
    ticker = (root.findtext(".//issuerTradingSymbol") or "").strip().upper()
    insider = (root.findtext(".//reportingOwner/reportingOwnerId/rptOwnerName") or "").strip()
    if not ticker or not insider:
        return None
    codes = {(c.text or "").strip()
             for c in root.findall(".//nonDerivativeTransaction//transactionCode")}
    return {"ticker": ticker, "insider": insider, "open_market_buy": "P" in codes}


def _prescored_event(filing: dict, subtype: str, score: int, direction: str,
                     ticker: str, text: str, rationale: str) -> Event:
    return Event(
        source="filing",
        source_id=filing["accession"],
        ts=time.time(),
        text=text,
        symbols=[ticker],
        url=filing["url"],
        meta={
            "subtype": subtype,
            "check_confirmation": True,
            "prescored": {
                "score": score,
                "tickers": [{"symbol": ticker, "direction": direction}],
                "category": subtype,
                "rationale": rationale,
            },
        },
    )


async def _fetch_primary_doc(client: httpx.AsyncClient, filing: dict) -> str:
    """First non-index .htm in the filing directory, tags stripped."""
    index = await _fetch_index(client, filing)
    for item in index:
        name = item.get("name", "")
        if name.endswith(".htm") and "index" not in name:
            resp = await client.get(_dir_url(filing) + name)
            resp.raise_for_status()
            return _TAG_RE.sub(" ", resp.text)
    return ""


async def _fetch_form4_xml(client: httpx.AsyncClient, filing: dict) -> str:
    index = await _fetch_index(client, filing)
    for item in index:
        if item.get("name", "").endswith(".xml"):
            resp = await client.get(_dir_url(filing) + item["name"])
            resp.raise_for_status()
            return resp.text
    return ""


async def _fetch_index(client: httpx.AsyncClient, filing: dict) -> list[dict]:
    resp = await client.get(_dir_url(filing) + "index.json")
    resp.raise_for_status()
    return resp.json().get("directory", {}).get("item", [])


def _dir_url(filing: dict) -> str:
    # .../data/1865433/000149315226028198/0001493152-26-028198-index.htm -> dir/
    return filing["url"].rsplit("/", 1)[0] + "/"


class _TickerMap:
    """CIK -> ticker, cached to disk next to the DB, refreshed daily."""

    def __init__(self):
        self.path = Path(settings.db_path).resolve().parent / "company_tickers.json"
        self.map: dict[str, str] = {}
        self.fetched_at = 0.0
        if self.path.exists():
            self._load(self.path.read_text())
            self.fetched_at = self.path.stat().st_mtime

    def lookup(self, cik: str) -> str | None:
        return self.map.get(str(int(cik)))

    async def refresh(self, client: httpx.AsyncClient) -> None:
        try:
            resp = await client.get(TICKER_MAP_URL)
            resp.raise_for_status()
            self.path.write_text(resp.text)
            self._load(resp.text)
            self.fetched_at = time.time()
            print(f"filings: ticker map loaded ({len(self.map)} companies)")
        except Exception as exc:
            if not self.map:
                raise
            print(f"filings: ticker map refresh failed, using cache ({exc!r})")

    async def refresh_if_stale(self, client: httpx.AsyncClient) -> None:
        if time.time() - self.fetched_at > 86400:
            await self.refresh(client)

    def _load(self, text: str) -> None:
        self.map = {str(int(v["cik_str"])): v["ticker"]
                    for v in json.loads(text).values()}


def _remember(cache: OrderedDict, key, max_size: int = 5000) -> None:
    cache[key] = True
    while len(cache) > max_size:
        cache.popitem(last=False)
