"""اختبارات تمنع رجوع تسريب البيانات التجريبية إلى مسار النشر.

تؤكد هذه الاختبارات على:
1) في وضع live لا يُستخدم DemoProvider إطلاقاً — أي فشل = رمز مستبعد.
2) الرموز الوهمية (NVEX/GNSX/BIOK/RUBY/SRNA) لا تظهر في أي مخرج حي.
3) كل صف في المخرجات يحمل synthetic=False وحقول المصادر الأربعة.
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.providers.base import ReverseSplitEvent, TickerProfile, ShortData
from app.providers.hub import DataHub, get_hub, FAKE_SYMBOLS
from app.strategy import engine as eng


# --------------------------------------------------------------------------- #
# 1) الوضع live لا يستخدم DemoProvider أبداً
# --------------------------------------------------------------------------- #
class TestLiveNoDemo:
    def test_live_mode_does_not_fallback_to_demo_on_failure(self, monkeypatch):
        """إذا فشل مزوّد Yahoo في الوضع live، لا نرجع للديمو — نرجع DataFrame فارغ."""
        hub = DataHub(mode="live")

        # نجعل yahoo.daily يرمي خطأً دائماً
        def failing_daily(sym, lookback):
            raise RuntimeError("yfinance محجوب في هذا الاختبار")

        if hub.yahoo is not None:
            monkeypatch.setattr(hub.yahoo, "daily", failing_daily)
        # نتجاوز فحص live_available بضبطه يدوياً
        monkeypatch.setattr(hub, "_live_probe", True)

        df = hub.daily("JZ", 260)
        assert df is not None
        assert len(df) == 0, "في وضع live الفاشل يجب أن نحصل على DataFrame فارغ، ليس بيانات ديمو"

    def test_live_splits_no_demo_fallback(self, monkeypatch):
        hub = DataHub(mode="live")
        monkeypatch.setattr(hub, "_live_probe", True)

        # ياهو لا يعيد splits، وsec كذلك
        if hub.yahoo is not None:
            monkeypatch.setattr(hub.yahoo, "splits", lambda sym: [])
        monkeypatch.setattr(hub.sec, "splits", lambda sym: [])

        events = hub.splits("JZ")
        assert events == [], "في وضع live لا بد من عدم الرجوع لـ demo عند غياب التقسيمات"

    def test_live_profile_returns_none_on_failure(self, monkeypatch):
        hub = DataHub(mode="live")
        monkeypatch.setattr(hub, "_live_probe", True)
        if hub.yahoo is not None:
            monkeypatch.setattr(hub.yahoo, "profile", lambda sym: None)
        monkeypatch.setattr(hub.sec, "profile", lambda sym: None)

        prof = hub.profile("FAKE")
        assert prof is None, "في وضع live يجب إعادة None عند فشل الملف التعريفي (لا ديمو)"

    def test_live_catalysts_no_demo(self, monkeypatch):
        hub = DataHub(mode="live")
        monkeypatch.setattr(hub, "_live_probe", True)
        if hub.yahoo is not None:
            monkeypatch.setattr(hub.yahoo, "catalysts", lambda sym: [])
        monkeypatch.setattr(hub.news, "catalysts", lambda sym: [])
        monkeypatch.setattr(hub.sec, "catalysts", lambda sym: [])

        cats = hub.catalysts("JZ")
        assert cats == [], "في وضع live الفاشل لا تُرجع محفزات ديمو"

    def test_live_short_data_no_demo(self, monkeypatch):
        hub = DataHub(mode="live")
        monkeypatch.setattr(hub, "_live_probe", True)
        if hub.yahoo is not None:
            monkeypatch.setattr(hub.yahoo, "short_data", lambda sym: None)
        monkeypatch.setattr(hub.finra, "short_volume_ratio", lambda sym, sessions=10: None)

        sd = hub.short_data("JZ")
        assert sd is None, "في وضع live قد تكون بيانات البيع المكشوف None (لا ديمو)"


# --------------------------------------------------------------------------- #
# 2) الرموز الوهمية لا تدخل الكون ولا تُعالَج في الوضع الحي
# --------------------------------------------------------------------------- #
class TestFakeSymbolsRejected:
    @pytest.mark.parametrize("symbol", ["NVEX", "GNSX", "BIOK", "RUBY", "SRNA"])
    def test_fake_symbol_rejected_in_live(self, monkeypatch, symbol):
        hub = DataHub(mode="live")
        monkeypatch.setattr(hub, "_live_probe", True)
        # حتى لو كان للياهو بيانات، الرمز يُرفض لأنه في قائمة المحظورين
        # (محاكاة لفشل daily لهذه الرموز)
        df = hub.daily(symbol, 260)
        assert len(df) == 0, f"الرمز الوهمي {symbol} يجب أن يُستبعد في الوضع الحي"

    def test_fake_symbols_not_in_universe(self, monkeypatch):
        hub = DataHub(mode="live")
        monkeypatch.setattr(hub, "_live_probe", True)
        univ = hub.universe(limit=200)
        for fake in FAKE_SYMBOLS:
            assert fake not in univ, f"الرمز الوهمي {fake} يجب ألّا يكون في الكون الحي"


# --------------------------------------------------------------------------- #
# 3) تحليل الرمز في الوضع الحي يُنتج synthetic=False مع حقول المصادر
# --------------------------------------------------------------------------- #
class TestLiveAnalysisHasSourceFields:
    def _make_hub_with_fake_data(self, monkeypatch):
        """يصنع hub بوضع live مع بيانات حقيقية مصغّرة (يومي + تقسيم + ملف تعريفي)."""
        hub = DataHub(mode="live")
        # نضمن أن effective_mode يُعيد live بغض النظر عن فحص الشبكة
        monkeypatch.setattr(hub, "_live_probe", True)
        monkeypatch.setattr(hub, "_probe_at", float("inf"))
        monkeypatch.setattr(type(hub), "live_available", lambda self, force=False: True)
        # بيانات سعر يومي وهمية لكن مصدرها marked as yahoo (لأغراض الاختبار)
        dates = pd.bdate_range(end=pd.Timestamp.today(), periods=200)
        prices = pd.Series([10.0 + (i % 30) * 0.1 for i in range(200)], index=dates)
        df = pd.DataFrame({
            "open": prices, "high": prices + 0.2, "low": prices - 0.2,
            "close": prices, "volume": [1_000_000] * 200,
        }, index=dates)
        if hub.yahoo is not None:
            monkeypatch.setattr(hub.yahoo, "daily", lambda sym, lb=260: df)
            monkeypatch.setattr(hub.yahoo, "intraday_4h",
                                lambda sym, lb=90: pd.DataFrame())
            monkeypatch.setattr(hub.yahoo, "splits", lambda sym: [
                ReverseSplitEvent(symbol=sym, date=dates[-25], ratio_str="1-for-10",
                                  factor=10.0, source="yahoo-finance")])
            monkeypatch.setattr(hub.yahoo, "profile", lambda sym: TickerProfile(
                symbol=sym, name="Test Corp", exchange="NASDAQ", sector="Tech",
                shares_out=1e7, float_shares=5e6, market_cap=5e7, avg_volume=1e6,
                source="yahoo-finance"))
            monkeypatch.setattr(hub.yahoo, "short_data", lambda sym: ShortData(
                symbol=sym, short_pct_float=15.0, source="yahoo-finance"))
            monkeypatch.setattr(hub.yahoo, "catalysts", lambda sym: [])
        monkeypatch.setattr(hub.sec, "splits", lambda sym: [])
        monkeypatch.setattr(hub.sec, "profile", lambda sym: None)
        monkeypatch.setattr(hub.sec, "catalysts", lambda sym: [])
        monkeypatch.setattr(hub.sec, "dilution", lambda sym: [])
        monkeypatch.setattr(hub.news, "catalysts", lambda sym: [])
        monkeypatch.setattr(hub.finra, "short_volume_ratio",
                            lambda sym, sessions=10: None)
        return hub

    def test_live_analysis_marks_synthetic_false(self, monkeypatch):
        hub = self._make_hub_with_fake_data(monkeypatch)
        a = eng.analyze_symbol("TEST", hub)
        assert a.synthetic is False, "التحليل الحي يجب أن يكون synthetic=False"

    def test_live_analysis_has_all_four_sources(self, monkeypatch):
        hub = self._make_hub_with_fake_data(monkeypatch)
        a = eng.analyze_symbol("TEST", hub)
        assert a.price_source, "price_source يجب أن يكون ممتلئاً"
        assert "demo" not in a.price_source
        assert a.fundamentals_source, "fundamentals_source يجب أن يكون ممتلئاً"
        # split_source/news_source قد تكونا فارغتين إن لم يوجد تقسيم/أخبار
        # لكن الحقل موجود
        assert hasattr(a, "split_source")
        assert hasattr(a, "news_source")

    def test_live_analysis_analysis_dict_has_synthetic_key(self, monkeypatch):
        hub = self._make_hub_with_fake_data(monkeypatch)
        a = eng.analyze_symbol("TEST", hub)
        d = a.to_dict()
        assert "synthetic" in d
        assert "price_source" in d
        assert "split_source" in d
        assert "fundamentals_source" in d
        assert "news_source" in d

    def test_failed_live_symbol_returns_rejected_not_demo(self, monkeypatch):
        """رمز يفشل في جلب أسعاره في الوضع live يجب أن يُستبعد لا أن يأتي من الديمو."""
        hub = DataHub(mode="live")
        monkeypatch.setattr(hub, "_live_probe", True)
        if hub.yahoo is not None:
            def failing(sym, *a, **kw):
                return pd.DataFrame()
            monkeypatch.setattr(hub.yahoo, "daily", failing)
            monkeypatch.setattr(hub.yahoo, "intraday_4h", failing)
        a = eng.analyze_symbol("BADSYM", hub)
        assert a.synthetic is False or a.synthetic == (hub.effective_mode == "demo")
        assert len(a.rejections) > 0
        assert not a.rs_found


# --------------------------------------------------------------------------- #
# 4) وضع auto يُعامل كـ live (لا تراجع للديمو)
# --------------------------------------------------------------------------- #
class TestAutoIsLive:
    def test_auto_mode_effective_is_live_when_available(self, monkeypatch):
        hub = DataHub(mode="auto")
        assert hub.mode == "live", "auto يجب أن يُترجم إلى live (لم نعد ندعم التراجع للديمو)"
