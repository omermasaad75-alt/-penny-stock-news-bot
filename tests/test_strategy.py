"""اختبارات الاستراتيجية الحالية: قاع ثانٍ / سحب سيولة + منطقة القمة ÷ 2 + أهداف 4×25%.

القسم الأول: محرّك الاستراتيجية (app/strategy/strategy_engine.py) على سلاسل مصنوعة يدوياً.
القسم الثاني: مُحوّل التحليل (app/strategy/engine.py) على بيانات الديمو.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.strategy import engine as eng
from app.strategy.strategy_engine import (
    StrategyConfig,
    _find_pattern,
    _post_peak_drawdown,
    _targets,
    evaluate_symbol,
)


# --------------------------------------------------------------------------- #
#  أدوات بناء السلاسل
# --------------------------------------------------------------------------- #
def mk(closes, spread: float = 0.0) -> pd.DataFrame:
    """شموع يومية من سلسلة إغلاقات: الافتتاح = إغلاق الشمعة السابقة."""
    c = np.asarray(closes, dtype=float)
    o = np.r_[c[0], c[:-1]]
    idx = pd.bdate_range(end="2026-10-14", periods=len(c))
    return pd.DataFrame({
        "open": o,
        "high": np.maximum(o, c) * (1 + spread),
        "low": np.minimum(o, c) * (1 - spread),
        "close": c,
        "volume": 1_000_000.0,
    }, index=idx)


def double_bottom_closes() -> list[float]:
    """قاع 1.50 ← ارتداد إلى 2.20 ← قاع 2 ≈ 1.55 (ضمن 4%) ← تعافٍ."""
    return (list(np.linspace(3.0, 1.50, 40))
            + list(np.linspace(1.50, 2.20, 10))[1:]
            + list(np.linspace(2.20, 1.55, 15))[1:]
            + list(np.linspace(1.55, 1.90, 12))[1:])


def decline_then_flat(peak=3.0, bottom=1.8, n=120) -> list[float]:
    """صعود إلى القمة ثم هبوط إلى القاع بعدها (هبوط حقيقي بعد القمة)."""
    return list(np.linspace(1.0, peak, 40)) + list(np.linspace(peak, bottom, 40))[1:] + [bottom] * (n - 80)


# --------------------------------------------------------------------------- #
#  1) محرّك الاستراتيجية
# --------------------------------------------------------------------------- #
class TestDrawdownAfterPeak:
    def test_drawdown_measured_from_peak_not_from_earlier_low(self):
        """القاع (1.0) قبل القمة (3.0) لا يُحسب هبوطاً؛ الهبوط الحقيقي من 3.0 إلى 2.0 = 33%."""
        closes = list(np.linspace(1.0, 3.0, 40)) + list(np.linspace(3.0, 2.0, 40)) + [2.0] * 20
        df = mk(closes)
        peak, post_low, dd = _post_peak_drawdown(df, StrategyConfig())
        assert peak == pytest.approx(3.0, rel=1e-3)
        assert post_low == pytest.approx(2.0, rel=1e-3)
        assert dd == pytest.approx(1 - 2.0 / 3.0, abs=1e-3)

    def test_33_percent_drop_fails_40_percent_rule(self):
        closes = list(np.linspace(1.0, 3.0, 40)) + list(np.linspace(3.0, 2.0, 40)) + [2.0] * 20
        res = evaluate_symbol(mk(closes), short_interest=5_000, has_news=True)
        assert any("لا هبوط كافٍ بعد القمة" in f for f in res["failed"])
        assert not any("سلوك سلبي سابق" in p for p in res["passed"])

    def test_real_40_percent_drop_passes(self):
        closes = list(np.linspace(1.0, 3.0, 40)) + list(np.linspace(3.0, 1.6, 40)) + [1.6] * 20
        res = evaluate_symbol(mk(closes), short_interest=5_000, has_news=True)
        assert any("سلوك سلبي سابق" in p for p in res["passed"])


class TestPattern:
    def test_double_bottom_found(self):
        df = mk(double_bottom_closes(), spread=0.01)
        pat = _find_pattern(df.reset_index(drop=True), StrategyConfig())
        assert pat["found"] is True
        assert pat["type"] in {"double_bottom", "liquidity_sweep"}
        assert pat["low1"] < pat["neckline"]
        assert abs(pat["low2"] - pat["low1"]) / pat["low1"] < 0.10

    def test_no_pattern_on_monotonic_decline(self):
        df = mk(list(np.linspace(3.0, 1.0, 120)), spread=0.01)
        pat = _find_pattern(df.reset_index(drop=True), StrategyConfig())
        assert pat["found"] is False

    def test_broken_pattern_is_not_counted_and_has_no_plan(self):
        closes = double_bottom_closes() + list(np.linspace(1.90, 1.20, 15))[1:]
        res = evaluate_symbol(mk(closes, spread=0.01), short_interest=5_000, has_news=True)
        assert res["pattern"].get("found") is False
        assert res["pattern"].get("broken") is True
        assert res["trade_plan"] is None
        assert any("النمط منكسر" in f for f in res["failed"])


class TestTradePlan:
    def test_targets_are_four_quarter_steps(self):
        plan = _targets(entry=2.0, low2=1.6, cfg=StrategyConfig())
        assert plan["valid"] is True
        assert plan["targets"] == pytest.approx([2.5, 3.0, 3.5, 4.0], rel=1e-6)
        assert plan["stop"] == pytest.approx(1.6 * 0.97, rel=1e-6)
        assert plan["position_size"] == int(50.0 / (2.0 - 1.6 * 0.97))

    def test_zero_or_tiny_risk_is_invalid_and_sizes_nothing(self):
        # الدخول على الوقف نفسه: المخاطرة صفر
        plan = _targets(entry=1.0038, low2=1.0348, cfg=StrategyConfig())
        assert plan["valid"] is False
        assert plan["position_size"] == 0

    def test_negative_risk_is_invalid(self):
        plan = _targets(entry=1.0, low2=1.5, cfg=StrategyConfig())
        assert plan["valid"] is False and plan["position_size"] == 0


class TestScoring:
    def test_breakdown_sums_to_score_and_weights_to_100(self):
        res = evaluate_symbol(mk(double_bottom_closes(), spread=0.01), short_interest=5_000, has_news=True)
        assert sum(b["weight"] for b in res["breakdown"]) == 100
        assert sum(b["points"] for b in res["breakdown"]) == pytest.approx(res["score"], abs=0.2)

    def test_unknown_short_and_news_are_neutral(self):
        closes = double_bottom_closes()
        known = evaluate_symbol(mk(closes, spread=0.01), short_interest=5_000, has_news=True)
        unknown = evaluate_symbol(mk(closes, spread=0.01), short_interest=None, has_news=None)
        assert unknown["score"] == pytest.approx(known["score"] - 8 * 0.5 - 4 * 0.5, abs=0.2)
        assert any("محايد" in u for u in unknown["unknown"])

    def test_nan_short_interest_is_neutral(self):
        closes = double_bottom_closes()
        res = evaluate_symbol(mk(closes, spread=0.01), short_interest=float("nan"), has_news=True)
        assert any("غير متوفر" in u for u in res["unknown"])

    def test_status_ready_requires_confirmation(self):
        res = evaluate_symbol(mk(double_bottom_closes(), spread=0.01), short_interest=5_000, has_news=True)
        if res["status"] == "READY":
            assert res["confirmation"]["confirmed"] is True
            assert res["score"] >= 75

    def test_price_out_of_range_excluded_with_full_schema(self):
        res = evaluate_symbol(mk(list(np.linspace(20, 25, 80))), short_interest=5_000, has_news=True)
        assert res["status"] == "EXCLUDED"
        assert res["breakdown"] == [] and res["trade_plan"] is None
        assert "criteria" in res

    def test_short_history_excluded_with_full_schema(self):
        res = evaluate_symbol(mk(list(np.linspace(1, 2, 30))))
        assert res["status"] == "EXCLUDED" and res["breakdown"] == []


class TestRobustness:
    def test_random_series_never_crash_and_keep_invariants(self):
        rng = np.random.default_rng(7)
        for _ in range(150):
            n = int(rng.integers(60, 220))
            c = np.clip(1.6 * np.exp(np.cumsum(rng.normal(-0.004, 0.07, n))), 0.05, 12)
            o = np.r_[c[0], c[:-1]]
            df = pd.DataFrame({
                "open": o,
                "high": np.maximum(o, c) * (1 + rng.uniform(0, 0.08, n)),
                "low": np.minimum(o, c) * (1 - rng.uniform(0, 0.08, n)),
                "close": c, "volume": 1e5,
            }, index=pd.bdate_range(end="2026-10-14", periods=n))
            res = evaluate_symbol(df, short_interest=float(rng.integers(0, 50_000)),
                                  has_news=bool(rng.integers(0, 2)))
            assert res["status"] in {"READY", "NEAR", "WATCH", "EXCLUDED"}
            if res["breakdown"]:
                assert sum(b["points"] for b in res["breakdown"]) == pytest.approx(res["score"], abs=0.2)
            plan = res.get("trade_plan")
            if plan and plan["valid"]:
                assert plan["risk_per_share"] > 0
                assert plan["position_size"] >= 0


# --------------------------------------------------------------------------- #
#  2) مُحوّل التحليل (engine.analyze_symbol) على بيانات الديمو
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def demo_hub():
    from app.providers.hub import get_hub
    return get_hub(mode="demo", refresh=True)


@pytest.mark.parametrize("symbol", ["JZ", "BRTX", "NVEX", "QLGN"])
def test_adapter_status_phase_and_flags_agree(demo_hub, symbol):
    a = eng.analyze_symbol(symbol, demo_hub)
    expected_phase = {"READY": 6, "NEAR": 5, "WATCH": 2, "EXCLUDED": 0}
    if a.strategy_status in expected_phase:
        assert a.phase == expected_phase[a.strategy_status]
        assert a.eligible == (a.strategy_status in {"READY", "NEAR"})
        assert a.watchlist == (a.strategy_status in {"READY", "NEAR", "WATCH"})
        assert a.lifecycle_ready == (a.strategy_status == "READY")
    assert a.phase_ar == eng.phase_info(a.phase)["ar"]


def test_adapter_checklist_and_breakdown_shape(demo_hub):
    a = eng.analyze_symbol("JZ", demo_hub)
    assert len(a.lifecycle_checks) == 6
    assert len(a.breakdown) == 9
    assert {b["key"] for b in a.breakdown} == set(StrategyConfig().weights)
    assert all(c["state"] in {"done", "pending"} for c in a.lifecycle_checks)


def test_adapter_levels_consistent_with_plan(demo_hub):
    for sym in ("JZ", "BRTX", "NVEX", "QLGN"):
        a = eng.analyze_symbol(sym, demo_hub)
        plan = a.trade_plan
        if not (plan and plan.get("valid")):
            assert a.levels.get("trigger") is None
            continue
        assert a.levels["trigger"] == plan["entry"]
        assert a.levels["stop"] == plan["stop"] < plan["entry"]
        t = [a.levels["targets"][f"t{i}"] for i in range(1, 5)]
        assert t == sorted(t) and len(set(t)) == 4


def test_adapter_has_no_legacy_gam_or_rs_decision_fields(demo_hub):
    d = eng.analyze_symbol("JZ", demo_hub).to_dict()
    for legacy in ("gam_candidate", "gam_reasons", "gam_rsi_scope", "rs_found", "rs_ratio_str"):
        assert legacy not in d


def test_adapter_missing_data_is_rejected_not_demo():
    from app.providers.hub import DataHub

    class EmptyHub(DataHub):
        def __init__(self):
            self.mode = "demo"
            self.rejections = {}

        @property
        def effective_mode(self):
            return "demo"

        def daily(self, symbol, lookback_days=260):
            return pd.DataFrame()

    a = eng.analyze_symbol("NOPE", EmptyHub())
    assert a.phase == 0
    assert a.rejection_codes == ["missing_price_data"]


def test_scan_sorts_by_phase_then_score(demo_hub):
    rows = eng.scan(["JZ", "BRTX", "NVEX", "QLGN", "UPC"], demo_hub)
    keys = [(-r.phase, -r.score) for r in rows]
    assert keys == sorted(keys)


# --------------------------------------------------------------------------- #
#  3) الفلاتر الصلبة والتحليل الفني (تبقى كما كانت في النظام)
# --------------------------------------------------------------------------- #
def test_liquidity_filter_excludes_but_keeps_analysis(demo_hub, monkeypatch):
    from app.config import STRATEGY
    monkeypatch.setattr(STRATEGY, "min_avg_dollar_volume", 1e15)
    a = eng.analyze_symbol("JZ", demo_hub)
    assert a.strategy_status == "EXCLUDED"
    assert a.phase == 0 and not a.eligible and not a.watchlist
    assert "liquidity" in a.rejection_codes
    # التحليل يبقى معروضاً رغم الاستبعاد
    assert a.rsi_now is not None and a.breakdown


def test_float_filter_excludes_when_cap_set(demo_hub, monkeypatch):
    from app.config import STRATEGY
    monkeypatch.setattr(STRATEGY, "max_float_shares", 1.0)
    a = eng.analyze_symbol("TRVN", demo_hub)
    assert a.strategy_status == "EXCLUDED" and "float" in a.rejection_codes
    assert not a.watchlist


def test_float_cap_disabled_by_default(demo_hub):
    from app.config import STRATEGY
    assert STRATEGY.max_float_shares == 0
    a = eng.analyze_symbol("TRVN", demo_hub)
    assert "float" not in a.rejection_codes


def test_technical_readings_and_patterns_present(demo_hub):
    a = eng.analyze_symbol("TRVN", demo_hub)
    assert a.rsi_now is not None and 0 <= a.rsi_now <= 100
    assert a.rvol is not None and a.rvol >= 0
    assert isinstance(a.macd_turning, bool) and isinstance(a.channel_broken, bool)
    assert isinstance(a.patterns_daily, list) and isinstance(a.patterns_4h, list)
    assert isinstance(a.h4, dict)


def test_report_has_technical_section(demo_hub):
    from app.reporting import analysis_to_markdown
    md = analysis_to_markdown(eng.analyze_symbol("TRVN", demo_hub))
    assert "## 5) المؤشرات الفنية" in md and "RSI" in md and "## 6) المؤشرات الأساسية" in md


# --------------------------------------------------------------------------- #
#  4) القمة الصحيحة ومنطقة القمة÷2 (تتّسق مع حساب الهبوط)
# --------------------------------------------------------------------------- #
def test_peak_is_true_highest_high_not_biggest_percent_spike():
    """الافتتاح 3 ثم الوصول 3.40 ⇒ القمة 3.40 والمنطقة 1.70 (وليس شمعة قفزت 20% من 1.00)."""
    from app.strategy.strategy_engine import _drop_zone
    idx = pd.bdate_range(end="2026-10-14", periods=5)
    df = pd.DataFrame({
        "open": [1.00, 1.00, 2.90, 3.00, 2.50],
        "high": [1.20, 1.10, 3.05, 3.40, 2.60],
        "low": [0.95, 0.98, 2.80, 2.95, 2.40],
        "close": [1.10, 1.05, 3.00, 3.10, 2.50],
        "volume": 1e6,
    }, index=idx)
    dz = _drop_zone(df, StrategyConfig())
    assert dz["spike_high"] == pytest.approx(3.40)
    assert dz["zone"] == pytest.approx(1.70)


def test_drop_zone_and_post_peak_drawdown_use_same_peak():
    from app.strategy.strategy_engine import _drop_zone, _post_peak_drawdown
    closes = list(np.linspace(1.0, 3.0, 40)) + list(np.linspace(3.0, 2.0, 40)) + [2.0] * 20
    df = mk(closes)
    dz = _drop_zone(df, StrategyConfig())
    peak, post_low, dd = _post_peak_drawdown(df, StrategyConfig())
    assert dz["spike_high"] == pytest.approx(peak)
    assert dz["zone"] == pytest.approx(peak / 2)
    assert dd == pytest.approx(1 - post_low / peak)


def test_price_near_half_peak_is_inside_drop_zone():
    """سعر قرب القاع الثاني داخل نطاق القمة÷2 يعطي نقاط منطقة الهبوط."""
    from app.strategy.strategy_engine import _drop_zone
    closes = list(np.linspace(1.0, 3.4, 40)) + list(np.linspace(3.4, 1.7, 40)) + [1.7] * 20
    df = mk(closes)
    dz = _drop_zone(df, StrategyConfig())
    assert dz["zone"] == pytest.approx(1.7, abs=1e-6)
    res = evaluate_symbol(df, short_interest=5_000, has_news=True)
    assert res["drop_zone"]["zone"] == pytest.approx(1.7, abs=1e-6)
    assert any("داخل منطقة القمة" in p for p in res["passed"])
