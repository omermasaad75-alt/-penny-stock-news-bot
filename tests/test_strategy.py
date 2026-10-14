"""اختبارات محرّك الاستراتيجية — الشروط، المراحل، التقييم، والمستويات."""
from __future__ import annotations

import pandas as pd
import pytest

from app.config import STRATEGY
from app.providers.base import ReverseSplitEvent, ShortData, TickerProfile
from app.providers.demo import DemoProvider, SCENARIOS
from app.providers.hub import DataHub
from app.strategy import engine as eng


@pytest.fixture(scope="module")
def hub() -> DataHub:
    return DataHub(mode="demo")


@pytest.fixture(scope="module")
def jz(hub) -> eng.Analysis:
    return eng.analyze_symbol("JZ", hub)


# ------------------------- شروط الاستراتيجية ------------------------- #
def test_reverse_split_within_window(jz):
    assert jz.rs_found
    assert jz.rs_ratio_str == "1-for-8"
    assert jz.rs_ratio == 8.0
    assert jz.rs_age_days is not None and jz.rs_age_days <= STRATEGY.rs_lookback_days


def test_spike_and_decline(jz):
    assert jz.rs_open == pytest.approx(3.0, abs=0.02)
    assert jz.spike_high == pytest.approx(3.40, abs=0.03)
    assert jz.spike_pct == pytest.approx(13.33, abs=1.0)
    assert jz.decline_pct is not None and abs(jz.decline_pct) >= STRATEGY.min_drop_pct


def test_bottom_and_base(jz):
    assert jz.bottom_price == pytest.approx(1.60, abs=0.02)
    assert jz.base_sessions >= STRATEGY.min_base_sessions
    assert jz.base_broken is False
    assert jz.base_range_pct is not None and jz.base_range_pct <= STRATEGY.base_tolerance_pct * 2


def test_oversold_and_recovery(jz):
    assert jz.rsi_trough is not None and jz.rsi_trough < STRATEGY.rsi_oversold
    assert jz.rsi_sessions_below_30 >= 3


def test_levels_are_consistent(jz):
    lv = jz.levels
    t = lv["targets"]
    assert lv["stop"] < lv["trigger"]
    assert t["t1_382"] < t["t2_618"] < t["t3_full"]
    assert lv["rr_t2"] > 1
    assert lv["stop_structural"] < lv["trigger"]


def test_jz_is_watchlisted(jz):
    assert jz.eligible is True
    assert jz.watchlist is True
    assert jz.phase >= 4
    assert jz.score > 60
    assert jz.grade in {"A+", "A", "B"}


# ------------------------- دورة الحياة (شرط «جاهز فنياً») ------------------------- #
def test_jz_full_lifecycle_sequence(jz):
    """JZ: ثبات ← انطلاقة ≈20% ← مقاومة قريبة ← سحب سيولة تحت الدعم ← استعادة."""
    assert jz.impulse_pct is not None and jz.impulse_pct >= STRATEGY.min_ready_impulse_pct
    assert abs(jz.impulse_pct - STRATEGY.ideal_impulse_pct) <= 8, "الانطلاقة يجب أن تكون قريبة من 20%"
    assert jz.resistance_level is not None
    assert jz.pullback_type == "liquidity_sweep"
    assert jz.sweep_low is not None and jz.sweep_low < jz.bottom_price   # سحب تحت الدعم
    assert jz.reclaim_sessions >= 1 and jz.sweep_low < jz.last_close     # عاد فوقه
    assert jz.lifecycle_ready is True
    assert jz.phase == 6
    assert all(c["ok"] for c in jz.lifecycle_checks)


def test_nvex_support_retest_variant(hub):
    """NVEX: النمط البديل — عودة لاختبار الدعم دون سحب سيولة، ثم استعادة."""
    a = eng.analyze_symbol("NVEX", hub)
    assert a.pullback_type == "support_retest"
    assert a.sweep_low is None
    assert a.return_to_support if hasattr(a, "return_to_support") else True
    assert a.lifecycle_ready is True and a.phase == 6


def test_partial_sequence_not_ready(hub):
    """SRNA: انطلاقة أقل من الحد (13%) ⇒ لا تكتمل الجاهزية الفنية."""
    a = eng.analyze_symbol("SRNA", hub)
    assert a.impulse_pct is not None
    assert a.impulse_pct < STRATEGY.min_ready_impulse_pct
    assert a.lifecycle_ready is False
    assert a.phase < 6


def test_lifecycle_checks_structure(jz):
    keys = [c["key"] for c in jz.lifecycle_checks]
    assert keys == ["support_hold", "impulse", "resistance", "pullback", "sweep", "reclaim"]
    for c in jz.lifecycle_checks:
        assert c["ar"] and c["std"] and c["detail"] and isinstance(c["ok"], bool)
        assert c["state"] in {"ok", "active", "pending"}


def test_support_hold_requires_three_sessions(hub):
    """WKSP: الثبات جلستان فقط (أقل من 3) ⇒ لا يُعتمد «جاهز فنياً» مهما اكتملت بقية الخطوات."""
    a = eng.analyze_symbol("WKSP", hub)
    hold = next(c for c in a.lifecycle_checks if c["key"] == "support_hold")
    assert hold["ok"] is False
    assert a.support_hold_ok is False
    assert a.lifecycle_ready is False
    assert a.phase < 6


def test_stage_reflects_incomplete_sequence(hub):
    """لا يجوز أن تحمل الأسهم غير المكتملة وصف «مكتمل»."""
    for sym in ("WKSP", "SRNA", "TGL", "HLIO", "DRTS"):
        a = eng.analyze_symbol(sym, hub)
        if not a.lifecycle_ready:
            assert not a.lifecycle_stage.startswith("مكتمل"), (sym, a.lifecycle_stage)


def test_nearest_resistance_is_pivot(jz):
    assert jz.resistance_kind == "pivot"
    assert jz.resistance_level is not None and jz.resistance_level < jz.impulse_high * 1.02


def test_levels_use_sweep_low_for_invalidation(jz):
    """وقف إلغاء السيناريو يجب أن يكون تحت قاع السحب عند حدوثه."""
    assert jz.levels["stop_structural"] < jz.sweep_low


def test_narrative_mentions_lifecycle(jz):
    joined = " ".join(jz.why_ar)
    assert "الانطلاقة" in joined
    assert "سحب سيولة" in joined
    assert "مقاومة" in joined


# ------------------------- المراحل والاستبعاد ------------------------- #
def test_phase_monotonic_with_conditions(hub):
    res = {a.symbol: a for a in eng.scan(hub.universe(30), hub)}
    # UPC: تقسيم أقدم من النافذة
    assert res["UPC"].phase == 0 and any("نافذة" in r for r in res["UPC"].rejections)
    # HLIO: هبوط مستمر بدون ثبات كافٍ
    assert res["HLIO"].phase <= 4
    # NVEX: انعكاس مؤكد ومؤهل
    assert res["NVEX"].phase == 6 and res["NVEX"].eligible
    # لا يوجد رمز مؤهل بلا حدث تقسيم
    for a in res.values():
        if a.eligible:
            assert a.rs_found
        if a.watchlist:
            assert a.base_sessions >= STRATEGY.min_base_sessions


def test_rejection_reasons_are_arabic(hub):
    a = eng.analyze_symbol("UPC", hub)
    assert a.rejections and any("تقسيم عكسي" in r for r in a.rejections)


def test_pick_reverse_split_filters():
    events = [
        ReverseSplitEvent(symbol="X", date=pd.Timestamp.now().normalize() - pd.Timedelta(days=200),
                          ratio_str="1-for-5", factor=5.0),
        ReverseSplitEvent(symbol="X", date=pd.Timestamp.now().normalize() - pd.Timedelta(days=30),
                          ratio_str="1-for-10", factor=10.0),
    ]
    ev, reasons = eng.pick_reverse_split(events, STRATEGY)
    assert ev is not None and ev.factor == 10.0
    assert any("نافذة" in r for r in reasons)


def test_ratio_out_of_range_rejected():
    ev = ReverseSplitEvent(symbol="Y", date=pd.Timestamp.now().normalize() - pd.Timedelta(days=5),
                           ratio_str="1-for-120", factor=120.0)
    picked, reasons = eng.pick_reverse_split([ev], STRATEGY)
    assert picked is None and any("النطاق" in r for r in reasons)


# ------------------------- التقييم والمخرجات ------------------------- #
def test_score_breakdown_sums_to_score(jz):
    total = sum(b["points"] for b in jz.breakdown) / sum(b["weight"] for b in jz.breakdown) * 100
    assert total == pytest.approx(jz.score, abs=0.6)


def test_narrative_and_action_present(jz):
    assert len(jz.why_ar) >= 5
    assert "اختراق" in jz.action_ar or "متابعة" in jz.action_ar


def test_fundamentals_present(jz):
    assert jz.shares_out and jz.float_shares
    assert jz.float_shares < jz.shares_out
    assert jz.short_pct_float is not None
    assert jz.days_to_cover is not None


def test_news_and_dilution_present(jz):
    assert jz.catalysts, "يجب رصد المحفزات"
    assert jz.dilution, "يجب رصد إفصاحات التخفيف"
    assert any(c["type"] == "earnings" for c in jz.catalysts)


def test_patterns_detected(jz):
    assert jz.patterns_daily, "يجب رصد نماذج فنية"
    names = {p["name"] for p in jz.patterns_daily}
    assert names & {"base", "volume_dryup", "descending_channel", "double_bottom",
                    "falling_wedge", "rsi_divergence", "higher_lows", "candlestick_reversal"}


def test_h4_analysis(jz):
    assert jz.h4.get("available") is True
    assert jz.h4.get("rsi") is not None
    assert jz.h4.get("trend") in {"up", "down", "neutral"}


def test_scan_is_deterministic(hub):
    a1 = eng.analyze_symbol("JZ", DataHub(mode="demo"))
    a2 = eng.analyze_symbol("JZ", DataHub(mode="demo"))
    assert a1.score == a2.score
    assert a1.bottom_price == a2.bottom_price
    assert a1.base_sessions == a2.base_sessions


def test_all_scenarios_run(hub):
    for sc in SCENARIOS:
        a = eng.analyze_symbol(sc.symbol, hub)
        assert a.symbol == sc.symbol
        assert a.last_close > 0
        assert a.phase in eng.PHASES
        assert 0 <= a.score <= 100


def test_resistance_must_stall_not_blow_through(hub):
    """WKSP: تجاوز المقاومة بقوة (> 3%) ⇒ لا تُعدّ «أقرب مقاومة مختبرة» ولا يكتمل التسلسل."""
    a = eng.analyze_symbol("WKSP", hub)
    res = next(c for c in a.lifecycle_checks if c["key"] == "resistance")
    assert res["ok"] is False
    assert a.resistance_tested is False
    assert a.lifecycle_ready is False


def test_sequence_order_is_enforced(jz):
    """الترتيب الزمني: ثبات الدعم ← الانطلاقة ← المقاومة ← العودة للدعم/السحب ← الاستعادة."""
    assert jz.bottom_date < jz.impulse_date
    assert jz.resistance_date <= jz.impulse_date
    assert jz.pullback_date >= jz.impulse_date
    if jz.sweep_low:
        assert jz.sweep_low < jz.bottom_price          # الفتيل كسر الدعم فعلاً
        assert jz.sweep_date >= jz.pullback_date


def test_stop_placed_under_sweep_low(jz):
    """وقف إلغاء السيناريو يجب أن يكون تحت قاع السحب (وإلا تحت الدعم)."""
    assert jz.levels["stop_structural"] < (jz.sweep_low or jz.bottom_price)


def test_ready_requires_base_hold_and_full_sequence(hub):
    """كل من يُصنّف «جاهز فنياً» لا بد أن يستوفي الثبات ≥3 جلسات والتسلسل كاملاً."""
    for sym in ("JZ", "NVEX", "BRTX", "FLNT", "SRNA", "HLIO", "WKSP"):
        a = eng.analyze_symbol(sym, hub)
        if a.phase == 6:
            assert a.lifecycle_ready is True
            assert a.support_hold_ok is True
            assert a.base_sessions >= STRATEGY.min_base_sessions
            assert all(c["ok"] for c in a.lifecycle_checks)
