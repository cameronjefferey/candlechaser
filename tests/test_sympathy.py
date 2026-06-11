from app.sympathy import merge_sympathy, sympathy_for


def test_basket_lookup():
    out = sympathy_for(["MRVL"])
    assert "NVDA" in out and "AVGO" in out
    assert "MRVL" not in out
    assert len(out) <= 6


def test_multi_basket_union():
    # NVDA is in ai_semis; COIN in crypto_proxies — union of both, minus alerted.
    out = sympathy_for(["NVDA", "COIN"], cap=20)
    assert "AMD" in out and "MSTR" in out
    assert "NVDA" not in out and "COIN" not in out


def test_unknown_symbol_no_sympathy():
    assert sympathy_for(["ZZZZTEST"]) == []


def test_merge_baskets_first_then_llm():
    out = merge_sympathy(["MRVL"], ["TSM", "XYZ"])
    assert out[0] == "NVDA"          # curated basket order wins
    assert "XYZ" in out or len(out) == 6  # LLM extras appended until cap
    assert "MRVL" not in out
    assert len(out) <= 6
    assert len(set(out)) == len(out)  # deduped
