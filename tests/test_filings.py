from pathlib import Path

from app.sources.filings import _company, _parse_atom, parse_form4
from app.store import Store

FIXTURES = Path(__file__).parent / "fixtures"


def test_atom_parse_groups_and_filters():
    filings = _parse_atom((FIXTURES / "edgar_current.atom").read_text())
    # Only handled forms survive; the raw feed is mostly 144s/13Fs/etc.
    assert all(f["form"] in {"8-K", "SC 13D", "SC 13D/A", "4", "S-1", "424B5"}
               for f in filings)
    # Entries are grouped: each accession appears once even though the feed
    # repeats it per role.
    accessions = [f["accession"] for f in filings]
    assert len(accessions) == len(set(accessions))
    for f in filings:
        assert f["accession"].count("-") == 2
        assert f["url"].startswith("https://www.sec.gov/")
        assert f["roles"]


def test_company_role_priority():
    filing = {"roles": {
        "Reporting": {"cik": "0000000001", "name": "Insider Person"},
        "Issuer": {"cik": "0000000002", "name": "Acme Corp"},
    }}
    assert _company(filing)["name"] == "Acme Corp"


def test_form4_option_exercise_is_not_a_buy():
    parsed = parse_form4((FIXTURES / "form4.xml").read_text())
    assert parsed["ticker"] == "MVIS"
    assert parsed["insider"] == "Markham Drew G"
    assert parsed["open_market_buy"] is False  # code M = option exercise


def test_form4_open_market_purchase():
    parsed = parse_form4((FIXTURES / "form4_purchase.xml").read_text())
    assert parsed["open_market_buy"] is True


def test_cluster_buy_threshold(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    window = 7 * 86400
    store.record_insider_buy("acc-1", "MVIS", "Insider One")
    assert len(store.insider_buyers("MVIS", window)) == 1  # single buy: no alert
    store.record_insider_buy("acc-1", "MVIS", "Insider One")  # same filing twice: ignored
    assert len(store.insider_buyers("MVIS", window)) == 1
    store.record_insider_buy("acc-2", "MVIS", "Insider Two")
    assert len(store.insider_buyers("MVIS", window)) == 2    # cluster threshold met
    assert store.insider_buyers("OTHER", window) == []
