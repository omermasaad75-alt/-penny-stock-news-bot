"""اختبارات مسار GAM الثانوي (near-miss) وحواجز عدم الترقية."""
from __future__ import annotations

from copy import deepcopy

import pytest

from app.config import STRATEGY
from app.providers.hub import DataHub
from app.reporting import analysis_to_markdown, watchlist_markdown
from app.strategy import engine as eng


def _near_miss(*, price: float = 1.25, codes: tuple[str, ...] = ("decline",)) -> eng.Analysis:
    """صف مكتمل البيانات؛ تُضاف إليه فقط أسباب الرفض المحددة للاختبار."""
    a = eng.Analysis(
        symbol="TEST",
        rs_found=True,
        last_close=price,
        spike_pct=10.0,
        decline_pct=-20.0 if "decline" in codes else -35.0,
        rsi_trough=35.0 if "rsi" in codes else 25.0,
        dollar_volume=1_000_000.0,
        base_broken=False,
        lifecycle_failed=False,
        synthetic=False,
    )
    messages = {
        "decline": "الهبوط 20% أقل من الحد المطلوب 30%",
        "rsi": "RSI لم ينزل تحت 30",
        "liquidity": "السيولة أقل من الحد",
        "spike": "صعود يوم التقسيم أقل من الحد",
        "base_broken": "كسر القاع المرجعي",
        "lifecycle_failure": "فشل تسلسل الانعكاس",
        "missing_rsi_data": "بيانات RSI غير كافية",
    }
    for code in codes:
        eng.add_rejection(a, code, messages[code])
    return a


@pytest.mark.parametrize("codes", [("decline",), ("rsi",), ("decline", "rsi")])
def test_gam_accepts_only_allowed_near_miss_failures(codes):
    a = _near_miss(codes=codes)
    assert eng.evaluate_gam_candidate(a, STRATEGY) is True
    assert a.gam_candidate is True
    assert a.eligible is False
    assert a.watchlist is False
    assert a.gam_reasons == a.rejections


@pytest.mark.parametrize("price", [STRATEGY.min_price, STRATEGY.max_price])
def test_gam_price_bounds_are_inclusive(price):
    a = _near_miss(price=price)
    assert eng.evaluate_gam_candidate(a, STRATEGY) is True


@pytest.mark.parametrize("price", [0.4999, 20.0001])
def test_gam_rejects_prices_outside_050_to_20(price):
    a = _near_miss(price=price)
    assert eng.evaluate_gam_candidate(a, STRATEGY) is False
    assert a.gam_candidate is False


@pytest.mark.parametrize(
    "blocker", ["liquidity", "spike", "base_broken", "lifecycle_failure", "missing_rsi_data"]
)
def test_gam_rejects_any_additional_exclusion(blocker):
    a = _near_miss(codes=("decline", blocker))
    if blocker == "liquidity":
        a.dollar_volume = 100_000
    if blocker == "spike":
        a.spike_pct = 4.0
    if blocker == "base_broken":
        a.base_broken = True
    if blocker == "lifecycle_failure":
        a.lifecycle_failed = True
    if blocker == "missing_rsi_data":
        a.rsi_trough = None
    assert eng.evaluate_gam_candidate(a, STRATEGY) is False
    assert a.gam_candidate is False


def test_gam_requires_recent_valid_reverse_split_and_complete_metrics():
    no_split = _near_miss()
    no_split.rs_found = False
    assert eng.evaluate_gam_candidate(no_split, STRATEGY) is False

    missing_decline = _near_miss()
    missing_decline.decline_pct = None
    assert eng.evaluate_gam_candidate(missing_decline, STRATEGY) is False

    unclassified = _near_miss()
    unclassified.rejections.append("سبب جديد غير مصنف")
    assert eng.evaluate_gam_candidate(unclassified, STRATEGY) is False


def test_gam_uses_current_scan_rsi_not_a_stale_rejection_flag():
    stale = _near_miss(codes=("rsi",))
    stale.rsi_trough = 25.0  # القراءة الحالية اجتازت RSI؛ رمز الرفض صار قديماً/غير متسق
    assert eng.evaluate_gam_candidate(stale, STRATEGY) is False
    assert stale.gam_candidate is False

    boundary = _near_miss(codes=("rsi",))
    boundary.rsi_trough = STRATEGY.rsi_oversold  # الشرط الصارم هو RSI < 30
    assert eng.evaluate_gam_candidate(boundary, STRATEGY) is True


def test_demo_candidates_stay_out_of_eligibility_and_watchlist():
    hub = DataHub(mode="demo")
    tgl = eng.analyze_symbol("TGL", hub, STRATEGY)
    biok = eng.analyze_symbol("BIOK", hub, STRATEGY)
    cheap = eng.analyze_symbol("QLGN", hub, STRATEGY)

    assert not tgl.gam_candidate and "short_shares" in tgl.rejection_codes
    assert not biok.gam_candidate and set(biok.rejection_codes) == {"decline", "rsi", "short_shares"}
    for a in (tgl, biok):
        assert a.eligible is False
        assert a.watchlist is False
        assert "غير مؤهل" in a.action_ar

    assert cheap.last_close < STRATEGY.min_price
    assert cheap.gam_candidate is False
    assert cheap.eligible is False and cheap.watchlist is False
    assert "price" in cheap.rejection_codes


def test_eligible_symbol_is_never_relabelled_as_gam():
    a = eng.analyze_symbol("JZ", DataHub(mode="demo"), STRATEGY)
    assert a.eligible is True
    assert a.watchlist is True
    assert a.gam_candidate is False


def test_gam_reports_are_separate_and_stateless(monkeypatch):
    hub = DataHub(mode="demo")
    eligible = eng.analyze_symbol("JZ", hub, STRATEGY)
    from app.providers.base import ShortData
    monkeypatch.setattr(hub, "short_data", lambda *a: ShortData("BIOK", short_shares=1000))
    gam = eng.analyze_symbol("BIOK", hub, STRATEGY)

    combined = watchlist_markdown([eligible, gam])
    assert "مرشحو GAM — مسار ثانوي غير مؤهل" in combined
    assert "**BIOK**" in combined
    assert "أفضل الفرص المؤهلة" in combined
    assert "لا توجد حالة رفض تاريخية محفوظة" in combined

    individual = analysis_to_markdown(gam)
    assert "وسم GAM — مسار ثانوي، غير مؤهل" in individual
    assert "لا توجد آلية تخزين أو استرجاع" in individual
    assert "ليست خطة تداول" in individual


def test_gam_evaluation_does_not_mutate_strict_flags():
    a = _near_miss(codes=("decline", "rsi"))
    before = (a.eligible, a.watchlist)
    eng.evaluate_gam_candidate(a, STRATEGY)
    assert (a.eligible, a.watchlist) == before == (False, False)

    # وحتى لو استُدعيت الدالة على فرصة مؤهلة، لا تعيد تصنيفها.
    eligible = deepcopy(a)
    eligible.eligible = True
    eligible.rejections.clear()
    eligible.rejection_codes.clear()
    assert eng.evaluate_gam_candidate(eligible, STRATEGY) is False
    assert eligible.eligible is True
