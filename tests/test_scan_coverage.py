"""اختبارات انحدار لتغطية المسح وتصنيف النتائج.

تمنع تكرار خطأين:
1) رفض سهم يحمل تقسيماً حديثاً صالحاً لمجرد وجود حدث تاريخي متجاهَل.
2) قصور اكتشاف الكون (صفحة واحدة، سعر < 5$، حجم يومي) دون شفافية حدود التغطية.
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.config import STRATEGY, settings
from app.providers.base import ReverseSplitEvent
from app.providers.hub import DataHub
from app.strategy import engine as eng


# --------------------------------------------------------------------------- #
# 1) الحدث التاريخي المتجاهَل لا يُبطل الأهلية
# --------------------------------------------------------------------------- #
@pytest.fixture()
def yahoo_env(tmp_path, monkeypatch):
    """مزوّد Yahoo معزول عن الشبكة: قوائم يدوية فارغة وsleep مُبطّل."""
    from app.providers import yahoo as yahoo_mod
    from app.providers.yahoo import YahooProvider

    monkeypatch.setattr(settings, "watchlist_file", tmp_path / "watchlist.csv")
    monkeypatch.setattr(settings, "universe_file", tmp_path / "universe.csv")
    monkeypatch.setattr(yahoo_mod.time, "sleep", lambda *_: None)
    return YahooProvider(), yahoo_mod


def test_market_candidates_fetches_all_pages_until_end(yahoo_env, monkeypatch):
    """screener يعيد 205 رموز مع cap=300 ⇒ صفحات بإزاحات [0, 100, 200] وtruncated=False."""
    prov, yahoo_mod = yahoo_env
    offsets: list[int | None] = []
    captured_queries: list = []
    symbols = [f"T{i:03d}" for i in range(205)]

    def fake_screen(query, offset=None, size=None, **kwargs):
        captured_queries.append(query)
        offsets.append(offset)
        start = offset or 0
        page = symbols[start:start + (size or 100)]
        # صيغة Yahoo الحقيقية: total = الإجمالي المطابق، count = عدد هذه الصفحة فقط
        return {"quotes": [{"symbol": s} for s in page],
                "count": len(page), "total": 205}

    monkeypatch.setattr(yahoo_mod.yf, "screen", fake_screen)
    out = prov.market_candidates(cap=300)

    assert offsets == [0, 100, 200], "يجب جلب الصفحات بإزاحات متتالية حتى نهاية النتائج"
    assert out == symbols
    disc = prov.candidate_discovery
    assert disc["truncated"] is False, "الوصول الفعلي لنهاية النتائج = غير مقتطع"
    assert disc["price_ceiling"] == STRATEGY.max_price
    assert disc["total_matching_screener"] == 205
    assert disc["pages_fetched"] == 3
    assert disc["candidates_collected"] == 205
    assert disc["candidate_cap"] == 300
    # الاستعلام: سعر > 0 حتى سقف الاستراتيجية (لا قيد 5$ ولا حجم يومي في الاكتشاف)
    qdict = captured_queries[0].to_dict()
    leaves = {str(op.get("operator")): op.get("operands") for op in qdict.get("operands", [])}
    assert leaves.get("LTE") == ["intradayprice", STRATEGY.max_price]
    assert leaves.get("GT") == ["intradayprice", 0]
    assert not any(
        isinstance(op, dict) and "dayvolume" in str(op.get("operands"))
        for op in qdict.get("operands", [])
    )


# --------------------------------------------------------------------------- #
# 3) بلوغ السقف = اقتطاع موثّق
# --------------------------------------------------------------------------- #
def test_market_candidates_cap_truncates_and_records_total(yahoo_env, monkeypatch):
    """screener يعلن 5000 نتيجة مع cap=200 ⇒ 200 رمزاً فقط وtruncated=True والكلي 5000."""
    prov, yahoo_mod = yahoo_env
    offsets: list[int | None] = []

    def fake_screen(query, offset=None, size=None, **kwargs):
        offsets.append(offset)
        start = offset or 0
        n = size or 100
        # صيغة Yahoo الحقيقية: total = الإجمالي المطابق (5000)، count = عدد هذه الصفحة
        return {"quotes": [{"symbol": f"S{start + i:05d}"} for i in range(n)],
                "count": n, "total": 5000}

    monkeypatch.setattr(yahoo_mod.yf, "screen", fake_screen)
    out = prov.market_candidates(cap=200)

    assert len(out) == 200
    assert offsets == [0, 100]
    disc = prov.candidate_discovery
    assert disc["truncated"] is True, "بلوغ السقف مع بقاء نتائج = اقتطاع يجب الإعلان عنه"
    assert disc["total_matching_screener"] == 5000
    assert disc["candidates_collected"] == 200
    assert disc["candidate_cap"] == 200
    assert disc["pages_fetched"] == 2


# --------------------------------------------------------------------------- #
# 4) scan_coverage: «لم يُفحص» ليست مستبعدة + partial
# --------------------------------------------------------------------------- #
def test_universe_scan_coverage_marks_unchecked_not_rejected(yahoo_env, monkeypatch):
    """عند بلوغ limit قبل فحص كل المرشحين: البقية «لم يُفحص» وpartial=True، بلا رفض فني."""
    prov, yahoo_mod = yahoo_env
    monkeypatch.setattr(prov, "market_candidates", lambda cap=None: [f"C{i:02d}" for i in range(10)])
    monkeypatch.setattr(prov, "daily", lambda sym, lb=320: pd.DataFrame(
        {"open": [1.0] * 60, "high": [1.1] * 60, "low": [0.9] * 60,
         "close": [1.05] * 60, "volume": [10_000.0] * 60},
        index=pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=60)))
    recent = pd.Timestamp.now().normalize() - pd.Timedelta(days=10)
    monkeypatch.setattr(prov, "splits", lambda sym: [ReverseSplitEvent(
        symbol=sym, date=recent, ratio_str="1-for-8", factor=8.0, source="test")])
    monkeypatch.setattr("app.providers.sec.SECProvider.splits", lambda self, sym: [])
    monkeypatch.setattr(yahoo_mod.time, "sleep", lambda *_: None)

    out = prov.universe(limit=3)
    cov = prov.scan_coverage

    assert out == ["C00", "C01", "C02"]
    assert cov["candidates_collected"] == 10
    assert cov["candidates_checked"] == 3
    assert cov["candidates_unchecked"] == 7
    assert cov["partial"] is True
    assert cov["stop_reason"] == "result_limit_reached"
    assert "C03" in cov["unchecked_symbols"]
    # «لم يُفحص» ليست مستبعدة فنياً
    assert "C03" not in prov.universe_rejections
    assert all(not r.startswith("انتهت ميزانية") for r in prov.universe_rejections.values())
