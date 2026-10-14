"""مزوّد البيانات التجريبي — Demo data provider (يعمل بدون انترنت).

يولّد جلسات كاملة (يومي + 4 ساعات) لسيناريوهات محاكية لواقع أسهم التقسيم العكسي:
تقسيم عكسي ← صعود محدود يوم التقسيم ← موجة هبوط قوية ← قاع ← ثبات ← انعكاس.

يُستخدم كوضع افتراضي حتى يتم تفعيل مزوّد بيانات السوق الحقيقي، وكنموذج
تحليلي مرجعي (sample analytical model) يمكن مقارنة نتائج النظام به.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import zlib

import numpy as np
import pandas as pd

from .base import (
    BaseProvider,
    Catalyst,
    DilutionSignal,
    ReverseSplitEvent,
    ShortData,
    TickerProfile,
)


# --------------------------------------------------------------------------- #
#  تعريف السيناريوهات
# --------------------------------------------------------------------------- #
@dataclass
class Scenario:
    symbol: str
    name: str
    sector: str
    industry: str
    exchange: str
    rs_offset: int                 # عدد الجلسات للخلف لتاريخ التقسيم (-1 = آخر جلسة)
    rs_ratio: float                # 8 = تقسيم عكسي 1 مقابل 8
    shares_out: float
    float_shares: float
    short_pct_float: float
    days_to_cover: float
    short_change: float            # تغيّر مراكز البيع المكشوف في آخر تقرير (٪)
    base_volume: float             # متوسط الحجم الطبيعي
    spike_pct: float               # نسبة صعود يوم التقسيم من سعر الافتتاح
    drop_pct: float                # نسبة الهبوط من القمة إلى القاع
    bottom_offset: int             # جلسة القاع
    rsi_trough: float              # أدنى RSI في الموجة
    rsi_now_target: float          # RSI اليوم
    recovery_strength: float       # 0 = لا انعكاس بعد، 1 = انعكاس مؤكد وقوي
    tail_gain_pct: float = 8.0     # نسبة التغيّر من القاع إلى آخر جلسة (سالب = استمرار الهبوط)
    rs_open: float | None = None   # سعر افتتاح يوم التقسيم (يُحسب تلقائياً إن لم يحدد)
    # ---- دورة الحياة بعد الثبات (شروط «جاهز فنياً» في ورقة الشروط) ----
    lifecycle: bool = True         # ثبات ← انطلاقة ~20% ← اختبار مقاومة ← عودة للدعم/سحب سيولة ← استعادة
    calm_sessions: int = 4         # جلسات الثبات قبل الانطلاقة
    impulse_pct: float = 20.0      # نسبة الانطلاقة من إغلاق الثبات
    impulse_sessions: int = 4
    resist_price: float | None = None   # المقاومة القريبة (تُشتق آلياً إن لم تحدد)
    pullback_sessions: int = 3
    pullback_target: str = "sweep"  # sweep = سحب سيولة تحت الدعم | support = إعادة اختبار الدعم
    sweep_depth_pct: float = 4.0    # عمق سحب السيولة تحت الدعم
    reclaim_sessions: int = 2
    price_high_note: str = ""
    catalysts: list[dict] = field(default_factory=list)
    dilution: list[dict] = field(default_factory=list)
    note_ar: str = ""
    pre_price: float = 3.2         # سعر ما قبل التقسيم (معدّل بأثر رجعي)
    bottom_price: float | None = None   # سعر القاع (يتحسب تلقائياً إن لم يحدد)


SCENARIOS: list[Scenario] = [
    # ================================================================ #
    #  JZ — النموذج المرجعي (مبني على المثال الذي ورد في ورقة الاستراتيجية)
    # ================================================================ #
    Scenario(
        symbol="JZ",
        name="JZ Holdings Inc.",
        sector="تقنية استهلاكية",
        industry="إلكترونيات",
        exchange="NASDAQ",
        rs_offset=-24, rs_ratio=8.0,
        shares_out=9_500_000, float_shares=3_200_000,
        short_pct_float=18.4, days_to_cover=3.1, short_change=+12.0,
        base_volume=1_450_000,
        spike_pct=13.33, drop_pct=52.9, bottom_offset=-16,
        rsi_trough=21.5, rsi_now_target=48.0, recovery_strength=0.9,
        tail_gain_pct=11.5, rs_open=3.00, pre_price=2.95, bottom_price=1.60,
        lifecycle=True, calm_sessions=4, impulse_pct=22.0, impulse_sessions=4,
        pullback_sessions=4, pullback_target="sweep",
        sweep_depth_pct=4.5, reclaim_sessions=2,
        note_ar=(
            "النموذج المرجعي الكامل: تقسيم عكسي 1:8، افتتاح 3.00$ → قمة 3.40$ (+13.3%) → "
            "هبوط 53% إلى قاع 1.60$ → ثبات 4 جلسات مع جفاف حجم → انطلاقة +22% إلى 1.96$ "
            "(اختبار مقاومة قريبة) → سحب سيولة تحت الدعم إلى 1.53$ والعودة فوقه → "
            "استعادة 1.78$: التسلسل مكتمل والسهم جاهز فنياً."
        ),
        catalysts=[
            {"offset": -12, "type": "offering", "sentiment": "negative", "impact": "medium",
             "title": "إيداع S-1 لتسجيل أسهم بيع المساهمين (تخفيف محتمل)"},
            {"offset": -8, "type": "compliance", "sentiment": "neutral", "impact": "medium",
             "title": "إشعار بورصة: استكمال شرط سعر الإقفال الأدنى بعد التقسيم العكسي"},
            {"offset": +29, "type": "earnings", "sentiment": "neutral", "impact": "high",
             "title": "موعد الإعلان عن نتائج الربع (متوقع)"},
            {"offset": +45, "type": "other", "sentiment": "positive", "impact": "medium",
             "title": "مؤتمر مستثمرين / عرض منتجات جديد (متوقع)"},
        ],
        dilution=[
            {"offset": -12, "kind": "S-1", "severity": "medium",
             "title": "S-1 لتسجيل أسهم بيع المساهمين — 2.4M سهم"},
            {"offset": -55, "kind": "ATM", "severity": "high",
             "title": "برنامج ATM نشط (اتفاقية بيع أسهم حتى 15M$)"},
        ],
    ),
    # ================================================================ #
    #  NVEX — حالة مكتملة (جاهز فنياً + ضغط بيع مكشوف عالٍ)
    # ================================================================ #
    Scenario(
        symbol="NVEX",
        name="Novex Bio Holdings",
        sector="رعاية صحية",
        industry="تقنية حيوية",
        exchange="NASDAQ",
        rs_offset=-36, rs_ratio=20.0,
        shares_out=6_800_000, float_shares=1_900_000,
        short_pct_float=31.2, days_to_cover=4.8, short_change=+22.0,
        base_volume=980_000,
        spike_pct=22.0, drop_pct=61.0, bottom_offset=-18,
        rsi_trough=16.8, rsi_now_target=55.0, recovery_strength=1.0,
        pre_price=4.8,
        tail_gain_pct=17.0, rs_open=4.92,
        lifecycle=True, calm_sessions=4, impulse_pct=20.0, impulse_sessions=4,
        pullback_sessions=4, pullback_target="support",
        reclaim_sessions=3,
        note_ar=(
            "حالة مكتملة: تقسيم عكسي 1:20، هبوط 61% إلى القاع، ثم ثبات على الدعم، "
            "انطلاقة +20% إلى 2.86$ عند مقاومة قريبة، ثم إعادة اختبار الدعم الرئيسي "
            "والاستعادة فوقه. مع فلوت 1.9M وبيع مكشوف 31% من الفلوت → أفضلية ضغط قصير."
        ),
        catalysts=[
            {"offset": -20, "type": "clinical", "sentiment": "positive", "impact": "high",
             "title": "نتائج إيجابية مبدئية لدراسة المرحلة الثانية"},
            {"offset": -9, "type": "compliance", "sentiment": "neutral", "impact": "low",
             "title": "استلام خطاب الامتثال لسعر الإقفال"},
            {"offset": +11, "type": "earnings", "sentiment": "neutral", "impact": "high",
             "title": "نتائج الربع (متوقع)"},
        ],
        dilution=[
            {"offset": -70, "kind": "ATM", "severity": "medium",
             "title": "اتفاقية ATM حتى 10M$"},
            {"offset": -30, "kind": "warrant", "severity": "medium",
             "title": "مذكرات شراء بسعر ممارسة 2.50$ (3.1M مذكرة)"},
        ],
    ),
    # ================================================================ #
    #  HLIO — تقسيم حديث جداً، الهبوط مستمر ولم يتكوّن قاع
    # ================================================================ #
    Scenario(
        symbol="HLIO",
        name="Helio Robotics Corp.",
        sector="صناعة",
        industry="آلات صناعية",
        exchange="NASDAQ",
        rs_offset=-10, rs_ratio=12.0,
        shares_out=4_100_000, float_shares=2_600_000,
        short_pct_float=9.1, days_to_cover=1.4, short_change=-3.0,
        base_volume=1_150_000,
        spike_pct=9.0, drop_pct=48.0, bottom_offset=-1,
        rsi_trough=24.0, rsi_now_target=24.0, recovery_strength=0.0,
        pre_price=5.4,
        tail_gain_pct=-11.0, rs_open=5.55,
        note_ar="تقسيم 1:12 حديث — الهبوط مستمر ويسجّل قيعان أدنى، لم يتكوّن قاع/ثبات بعد.",
        catalysts=[
            {"offset": -4, "type": "offering", "sentiment": "negative", "impact": "high",
             "title": "إتمام طرح مباشر بقيمة 8M$ (تخفيف فوري)"},
        ],
        dilution=[
            {"offset": -4, "kind": "424B5", "severity": "high",
             "title": "طرح مباشر 8M$ بسعر 2.10$ للسهم"},
        ],
    ),
    # ================================================================ #
    #  SRNA — قاعدة 3 جلسات، RSI تحت 35 لكن التحسّن لم يتأكد بعد
    # ================================================================ #
    Scenario(
        symbol="SRNA",
        name="Serena Digital Media",
        sector="خدمات اتصالات",
        industry="إعلام رقمي",
        exchange="NASDAQ",
        rs_offset=-29, rs_ratio=4.0,
        shares_out=12_400_000, float_shares=8_900_000,
        short_pct_float=6.2, days_to_cover=0.9, short_change=+1.5,
        base_volume=610_000,
        spike_pct=11.0, drop_pct=40.0, bottom_offset=-9,
        rsi_trough=26.5, rsi_now_target=40.0, recovery_strength=0.45,
        pre_price=1.85,
        tail_gain_pct=6.0, rs_open=1.88,
        lifecycle=True, calm_sessions=2, impulse_pct=12.0, impulse_sessions=3,
        pullback_sessions=2, pullback_target="support", reclaim_sessions=1,
        note_ar="ثبات 3 جلسات على القاع مع RSI أقل من 30 سابقاً، لكن التحسّن الفني بطيء.",
        catalysts=[
            {"offset": -16, "type": "earnings", "sentiment": "negative", "impact": "medium",
             "title": "خسارة ربع سنوية أوسع من المتوقع"},
            {"offset": +21, "type": "earnings", "sentiment": "neutral", "impact": "medium",
             "title": "نتائج الربع القادم (متوقع)"},
        ],
        dilution=[{"offset": -60, "kind": "shelf", "severity": "low",
                   "title": "رف تسجيل S-3 مسجّل مسبقاً"}],
    ),
    # ================================================================ #
    #  BRTX — حالة فنية جيدة لكن تخفيف خطر جداً (طرح مباشر أمس)
    # ================================================================ #
    Scenario(
        symbol="BRTX",
        name="Bretex Energy Solutions",
        sector="طاقة",
        industry="خدمات النفط",
        exchange="NASDAQ",
        rs_offset=-19, rs_ratio=40.0,
        shares_out=2_300_000, float_shares=1_450_000,
        short_pct_float=14.7, days_to_cover=2.2, short_change=+8.0,
        base_volume=520_000,
        spike_pct=18.0, drop_pct=55.0, bottom_offset=-12,
        rsi_trough=18.2, rsi_now_target=38.0, recovery_strength=0.55,
        pre_price=9.6,
        tail_gain_pct=4.0, rs_open=9.85,
        lifecycle=True, calm_sessions=3, impulse_pct=19.0, impulse_sessions=3,
        pullback_sessions=3, pullback_target="support", reclaim_sessions=1,
        note_ar=("تشبع بيعي متطرف وثبات جيد + انطلاقة 19% واختبار المقاومة، "
                 "والسهم الآن في طريق العودة لاختبار الدعم — لكن مخاطر التخفيف مرتفعة جداً (طرح جديد)."),
        catalysts=[
            {"offset": -1, "type": "offering", "sentiment": "negative", "impact": "high",
             "title": "إيداع 424B5 لطرح 4.5M$ بسعر مخفّض عن السوق"},
            {"offset": +7, "type": "offering", "sentiment": "negative", "impact": "medium",
             "title": "احتمال إغلاق الطرح وضغط بيع المؤسسات المشاركة"},
        ],
        dilution=[
            {"offset": -1, "kind": "424B5", "severity": "high",
             "title": "طرح 4.5M$ — خصم 22% عن آخر إقفال"},
            {"offset": -34, "kind": "warrant", "severity": "high",
             "title": "مذكرات شراء بسعر 1.20$ قابلة للتنفيذ فوراً"},
        ],
    ),
    # ================================================================ #
    #  UPC — تقسيم قديم خارج النافذة (يُرفض)
    # ================================================================ #
    Scenario(
        symbol="UPC",
        name="United Pacific Container",
        sector="نقل",
        industry="شحن",
        exchange="NYSE American",
        rs_offset=-75, rs_ratio=15.0,
        shares_out=7_600_000, float_shares=5_100_000,
        short_pct_float=4.0, days_to_cover=0.6, short_change=-8.0,
        base_volume=430_000,
        spike_pct=12.0, drop_pct=44.0, bottom_offset=-52,
        rsi_trough=23.0, rsi_now_target=58.0, recovery_strength=0.7,
        pre_price=6.2,
        tail_gain_pct=19.0, rs_open=6.35,
        lifecycle=True, impulse_pct=21.0, impulse_sessions=4,
        pullback_target="support", reclaim_sessions=2,
        note_ar="تقسيم عكسي عمره 75 يوماً — خارج نافذة الشهرين المطلوبة في الاستراتيجية.",
    ),
    # ================================================================ #
    #  TGL — فشل شرط التشبع البيعي (RSI لم ينزل تحت 30)
    # ================================================================ #
    Scenario(
        symbol="TGL",
        name="Tangle Logistics Group",
        sector="نقل",
        industry="لوجستيات",
        exchange="NASDAQ",
        rs_offset=-27, rs_ratio=3.0,
        shares_out=18_200_000, float_shares=14_500_000,
        short_pct_float=3.1, days_to_cover=0.4, short_change=0.0,
        base_volume=380_000,
        spike_pct=6.0, drop_pct=31.0, bottom_offset=-8,
        rsi_trough=31.0, rsi_now_target=41.0, recovery_strength=0.4,
        pre_price=1.42,
        tail_gain_pct=4.0, rs_open=1.44,
        lifecycle=False,
        note_ar="تحقق الهبوط (-31%) لكن RSI لم ينزل تحت 30 → شرط التشبع البيعي غير محقق.",
    ),
]


def _seed(*parts: str) -> int:
    """بذرة ثابتة (crc32) — تضمن ثبات البيانات التجريبية بين كل تشغيل."""
    return zlib.crc32("::".join(parts).encode("utf-8")) % (2**32)


# --------------------------------------------------------------------------- #
#  أدوات التقويم
# --------------------------------------------------------------------------- #
def _last_session(now: date | None = None) -> pd.Timestamp:
    """آخر جلسة مكتملة (نتجاهل اليوم الجاري لأنه قد يكون غير مغلق)."""
    now = now or date.today()
    ts = pd.Timestamp(now)
    return (ts - pd.tseries.offsets.BDay(1)).normalize()


def _session_index(periods: int = 420, end: pd.Timestamp | None = None) -> pd.DatetimeIndex:
    end = end or _last_session()
    return pd.bdate_range(end=end, periods=periods)


def _px(idx: pd.DatetimeIndex, offset: int, default=None):
    """سعر/تاريخ جلسة بإزاحة سالبة من آخر جلسة."""
    try:
        return idx[offset]
    except IndexError:
        return default


# --------------------------------------------------------------------------- #
#  محرّك التوليد
# --------------------------------------------------------------------------- #
def _fit_lengths(lens: list[int], target: int) -> list[int]:
    """توزيع أطوال المقاطع على عدد الجلسات المتاح مع الحفاظ على النِسَب."""
    n = len(lens)
    if target <= 0:
        return [0] * n
    if sum(lens) == target:
        return list(lens)
    if target < n:                      # لا يكفي العدد — ندمج المقاطع الأخيرة
        out = [1] * n
        for i in range(target, n):
            out[i] = 0
        return out
    scaled = [max(1, int(round(x * target / sum(lens)))) for x in lens]
    # تصحيح الفرق على أكبر مقطع
    diff = target - sum(scaled)
    order = sorted(range(n), key=lambda i: -lens[i])
    k = 0
    while diff != 0:
        i = order[k % n]
        if diff > 0:
            scaled[i] += 1
            diff -= 1
        elif scaled[i] > 1:
            scaled[i] -= 1
            diff += 1
        k += 1
        if k > 200:
            break
    return scaled


def _lifecycle_tail(sc: Scenario, support: float, base_close: float, tail_len: int,
                    rng: np.random.Generator) -> tuple[np.ndarray, dict]:
    """بناء الجزء الأخير من السلسلة وفق دورة حياة الاستراتيجية الكاملة.

    المقاطع: [ثبات] ← [انطلاقة ~20%] ← [اختبار مقاومة] ← [عودة للدعم / سحب سيولة] ← [استعادة]
    Returns: (closes, meta) حيث meta يحمل مؤشرات نسبية للمقاطع المهمة.
    """
    imp_high = base_close * (1 + sc.impulse_pct / 100.0)
    resist = float(sc.resist_price) if sc.resist_price else imp_high * 1.005
    sweep = str(sc.pullback_target).lower() == "sweep"
    pull_low = support * (1 - sc.sweep_depth_pct / 100.0) if sweep else support * 1.012
    reclaim = max(support * 1.045, base_close * 1.02)

    lens = _fit_lengths(
        [max(1, sc.calm_sessions), max(2, sc.impulse_sessions), 1,
         max(1, sc.pullback_sessions), max(1, sc.reclaim_sessions)],
        tail_len,
    )
    calm_n, imp_n, test_n, pull_n, recl_n = lens
    closes: list[float] = []

    # (1) الثبات على الدعم
    for _ in range(calm_n):
        closes.append(float(np.clip(base_close * (1 + rng.normal(0, 0.009)),
                                    support * 1.004, base_close * 1.05)))
    calm_end = len(closes)

    # (2) الانطلاقة نحو المستهدف (~20%)
    for k in range(1, imp_n + 1):
        u = (k / imp_n) ** 0.85
        closes.append(base_close + (imp_high - base_close) * u)
    imp_end = len(closes)

    # (3) اختبار المقاومة القريبة
    for _ in range(test_n):
        closes.append(imp_high * (1 + rng.normal(0, 0.004)))
    test_end = len(closes)

    # (4) العودة لاختبار الدعم الرئيسي / سحب السيولة تحته
    for k in range(1, pull_n + 1):
        u = (k / pull_n) ** 0.9
        closes.append(imp_high + (pull_low - imp_high) * u)
    if sweep and pull_n:                      # شمعة السحب تغلق فوق الدعم (استعادة فورية)
        closes[-1] = support * 1.015
    pull_end = len(closes)

    # (5) الاستعادة فوق الدعم
    start = closes[-1] if closes else base_close
    for k in range(1, recl_n + 1):
        u = k / max(1, recl_n)
        closes.append(start + (reclaim - start) * u)

    arr = np.array(closes[:tail_len], dtype=float)
    meta = {
        "imp_high": round(float(imp_high), 4),
        "resist": round(float(resist), 4),
        "pull_low": round(float(pull_low), 4),
        "sweep": bool(sweep),
        "calm_end": calm_end, "imp_end": imp_end, "test_end": test_end,
        "pull_end": pull_end, "total": len(arr),
        "imp_bar": max(0, imp_end - 1),
        "test_bar": max(0, test_end - 1),
        "pull_bar": max(0, pull_end - 1),
    }
    if len(arr) < tail_len:
        arr = np.concatenate([arr, np.full(tail_len - len(arr), arr[-1] if len(arr) else base_close)])
    return arr, meta


def _build_daily(sc: Scenario, idx: pd.DatetimeIndex, seed: int | None = None) -> pd.DataFrame:
    """توليد جلسات يومية تحاكي دورة استراتيجية كاملة.

    البنية:  قبل التقسيم (تذبذب) ← يوم التقسيم (قفزة وذروة حجم)
             ← موجة هبوط متذبذبة ← قاع ← قاعدة/ثبات مع جفاف حجم ← تعافٍ.
    """
    rng = np.random.default_rng(seed if seed is not None else _seed(sc.symbol, "daily"))
    n = len(idx)
    rs_i = int(max(40, min(n + sc.rs_offset, n - 4)))
    bottom_i = int(max(rs_i + 4, min(n + sc.bottom_offset, n - 1)))

    rs_open = float(sc.rs_open) if sc.rs_open else round(max(0.35, sc.pre_price * (1 + rng.uniform(0.01, 0.05))), 4)
    spike_high = round(rs_open * (1 + sc.spike_pct / 100.0), 4)
    bottom = float(sc.bottom_price) if sc.bottom_price else round(
        max(0.20, spike_high * (1 - sc.drop_pct / 100.0)), 4)
    real_drop = (spike_high - bottom) / spike_high * 100.0

    closes = np.zeros(n)
    # ---------- (أ) قبل التقسيم: تذبذب هابط ببطء ----------
    start_px = sc.pre_price * rng.uniform(1.25, 1.95)
    legacy = np.exp(np.linspace(np.log(start_px), np.log(sc.pre_price), max(1, rs_i)))
    legacy = legacy * (1 + rng.normal(0, 0.010, max(1, rs_i)))
    closes[:rs_i] = legacy
    # ---------- (ب) يوم التقسيم ----------
    rs_close = rs_open * float(rng.uniform(0.97, 1.05))
    rs_close = min(rs_close, spike_high * 0.98)
    closes[rs_i] = rs_close
    # ---------- (ج) الموجة الهابطة مع ارتدادات مضبوطة ----------
    span = max(1, bottom_i - rs_i)
    u = np.linspace(0.0, 1.0, span + 1)
    eased = u ** 0.85
    trend = np.exp((1 - eased) * np.log(rs_close) + eased * np.log(bottom * 1.03))
    amp = 0.07 + 0.035 * (1 - sc.recovery_strength)
    cycles = 3.2 + rng.uniform(-0.7, 0.7)
    bounce = 1 + amp * np.sin(2 * np.pi * cycles * u) * (1 - 0.45 * u)
    # ضجيج ارتباطي (AR-1) يُنتج أياماً صاعدة متفرقة داخل الهبوط → RSI واقعي لا متطرف
    ar = np.zeros(span + 1)
    innov = rng.normal(0, 0.018, span + 1)
    for k in range(1, span + 1):
        ar[k] = 0.45 * ar[k - 1] + innov[k]
    path = trend * bounce * (1 + ar)
    path[-1] = bottom * 1.03
    closes[rs_i : bottom_i + 1] = path
    # ---------- (د) القاع ثم الثبات ثم دورة الحياة الكاملة ---------- #
    tail_len = n - bottom_i - 1
    calm_len = 0
    life_meta: dict = {}
    if tail_len > 0:
        if sc.tail_gain_pct < 0 or not sc.lifecycle:
            # استمرار الهبوط أو قاعدة بسيطة بدون انطلاقة
            if sc.tail_gain_pct >= 0:
                calm = bottom * (1.012 + rng.normal(0, 0.010, tail_len))
                calm = np.clip(calm, bottom * 0.998, bottom * (1 + sc.tail_gain_pct / 100.0))
                tail = calm
            else:
                dec = np.exp(np.linspace(0, np.log(1 + sc.tail_gain_pct / 100.0), tail_len))
                tail = bottom * dec * (1 + rng.normal(0, 0.010, tail_len))
        else:
            tail, life_meta = _lifecycle_tail(sc, bottom, bottom * 1.03, tail_len, rng)
        closes[bottom_i + 1 :] = tail[:tail_len]
    closes[bottom_i] = bottom * 1.03
    closes = np.clip(closes, 0.08, None)

    # ---------- OHLC ----------
    opens = np.empty(n)
    opens[0] = closes[0] * rng.uniform(0.99, 1.01)
    opens[1:] = closes[:-1] * (1 + rng.normal(0, 0.014, n - 1))
    opens[rs_i] = rs_open
    if bottom_i + 1 < n:
        opens[bottom_i + 1] = min(opens[bottom_i + 1], bottom * 1.05)
        opens[bottom_i + 1] = max(opens[bottom_i + 1], bottom * 0.985)
    highs = np.maximum(opens, closes) * (1 + np.abs(rng.normal(0, 0.014, n)))
    lows = np.minimum(opens, closes) * (1 - np.abs(rng.normal(0, 0.014, n)))
    highs[rs_i] = spike_high
    lows[rs_i] = min(rs_open, rs_close) * float(rng.uniform(0.975, 0.995))
    lows[bottom_i] = bottom
    highs[bottom_i] = max(highs[bottom_i], bottom * 1.05)
    # --- ضبط مقاطع دورة الحياة على الشموع (انطلاقة/مقاومة/سحب/استعادة) ---
    if life_meta:
        imp_bar = bottom_i + 1 + life_meta.get("imp_bar", 0)
        test_bar = bottom_i + 1 + life_meta.get("test_bar", 0)
        pull_bar = bottom_i + 1 + life_meta.get("pull_bar", 0)
        imp_high = float(life_meta.get("imp_high") or 0)
        resist = float(life_meta.get("resist") or 0)
        if 0 <= imp_bar < n and imp_high:
            highs[imp_bar] = max(highs[imp_bar], imp_high)
            closes[imp_bar] = min(closes[imp_bar], imp_high)
        if 0 <= test_bar < n and resist and resist <= imp_high * 1.02:
            highs[test_bar] = max(highs[test_bar], resist)
        if life_meta.get("sweep") and 0 <= pull_bar < n:
            lows[pull_bar] = float(life_meta.get("pull_low") or lows[pull_bar])
            closes[pull_bar] = max(closes[pull_bar], bottom * 1.012)   # إغلاق فوق الدعم
            opens[pull_bar] = max(opens[pull_bar], bottom * 1.005)
        if 0 <= pull_bar < n:
            highs[pull_bar] = max(highs[pull_bar], opens[pull_bar], closes[pull_bar])
    # قمة ما بعد التقسيم يجب أن تكون هي الأعلى حتى تكوين القاع
    if bottom_i > rs_i:
        highs[rs_i + 1 : bottom_i + 1] = np.minimum(
            highs[rs_i + 1 : bottom_i + 1], spike_high * 0.985)
    # قيود منطقية على ما قبل التقسيم (الأسعار معدّلة أثر رجعياً)
    if rs_i > 0:
        cap = sc.pre_price * 1.55
        highs[:rs_i] = np.minimum(highs[:rs_i], cap)
        closes[:rs_i] = np.minimum(closes[:rs_i], cap)
    # قيود مرحلة القاعدة: القاع لا يُكسر (عند وجود سيناريو انعكاس)
    if sc.tail_gain_pct >= 0:
        sweep_floor: float | None = None
        sweep_bar_abs: int | None = None
        if life_meta and life_meta.get("sweep"):
            sweep_floor = float(life_meta.get("pull_low") or bottom)
            sweep_bar_abs = bottom_i + 1 + int(life_meta.get("pull_bar") or 0)
        for i in range(bottom_i + 1, n):
            opens[i] = max(opens[i], bottom * 1.001)
            closes[i] = max(closes[i], bottom * 1.001)
            floor = bottom                        # القاع لا يُكسر إلا في شمعة سحب السيولة
            if sweep_bar_abs is not None and i == sweep_bar_abs and sweep_floor:
                floor = sweep_floor
            lows[i] = max(min(lows[i], opens[i], closes[i]), floor)
            highs[i] = max(highs[i], opens[i], closes[i], lows[i])
        opens[bottom_i] = max(opens[bottom_i], bottom)
        closes[bottom_i] = max(closes[bottom_i], bottom)
        lows[bottom_i] = bottom
        highs[bottom_i] = max(highs[bottom_i], opens[bottom_i], closes[bottom_i], bottom)

    else:
        for i in range(bottom_i + 1, n):
            closes[i] = min(closes[i], bottom * 1.02)   # استمرار الهبوط

    # ضبط نهائي لضمان صحة كل شمعة: high ≥ max(open,close) و low ≤ min(open,close)
    opens = np.maximum(opens, 0.01)
    closes = np.maximum(closes, 0.01)
    highs = np.maximum(highs, np.maximum(opens, closes))
    lows = np.minimum(lows, np.minimum(opens, closes))
    lows = np.maximum(lows, 0.01)

    # ---------- الحجم ----------
    vol = np.empty(n)
    for i in range(n):
        if i < rs_i:
            mult = rng.uniform(0.75, 1.3)
        elif i == rs_i:
            mult = rng.uniform(4.5, 7.5)
        elif i <= bottom_i:
            frac = (i - rs_i) / max(1, bottom_i - rs_i)
            mult = 1.05 + 1.5 * (1 - frac) + rng.uniform(0.0, 0.6)
            if i >= bottom_i - 1:
                mult = rng.uniform(1.6, 2.8)
        else:
            pos = i - bottom_i
            if life_meta:
                calm_end = life_meta.get("calm_end", 3)
                imp_end = life_meta.get("imp_end", calm_end + 3)
                pull_bar = life_meta.get("pull_bar", imp_end)
                if pos <= calm_end:
                    mult = rng.uniform(0.32, 0.6)             # جفاف حجم داخل الثبات
                elif pos <= imp_end:
                    mult = rng.uniform(1.6, 2.6)              # الانطلاقة بحجم متزايد
                elif pos < pull_bar:
                    mult = rng.uniform(0.9, 1.4)              # اختبار المقاومة
                elif pos == pull_bar:
                    mult = rng.uniform(2.2, 3.6) if sc.pullback_target == "sweep" else rng.uniform(1.1, 1.7)
                else:
                    mult = rng.uniform(1.4, 2.4)              # الاستعادة
            else:
                k = (pos - calm_len) / max(1, (n - bottom_i - calm_len))
                base_component = 1.0 + 1.3 * k
                mult = base_component * (0.5 + 1.1 * sc.recovery_strength) + rng.uniform(0.0, 0.5)
        vol[i] = max(12_000.0, sc.base_volume * mult)

    df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": vol}, index=idx)
    df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].round(4)
    df["volume"] = df["volume"].round(0)
    if life_meta:
        df.attrs.update({
            "impulse_high": life_meta.get("imp_high"),
            "resistance": life_meta.get("resist"),
            "sweep_low": life_meta.get("pull_low") if life_meta.get("sweep") else None,
            "pullback_target": sc.pullback_target,
        })
    df.attrs.update({
        "rs_price": round(float(rs_open), 4), "spike_high": round(float(spike_high), 4),
        "bottom": round(float(bottom), 4), "drop_pct": real_drop,
        "rs_date": str(idx[rs_i].date()), "bottom_date": str(idx[bottom_i].date()),
        "rs_ratio": sc.rs_ratio,
    })
    return df


def _build_4h(daily: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """توليد شمعتين لكل جلسة (فريم 4 ساعات) بتوافق تام مع الشمعة اليومية.

    التوافق المضمون: الافتتاح/الإقفال/الأعلى/الأدنى/الحجم للفريم الأصغر
    تُعاد تجميعها لتساوي الشمعة اليومية تماماً.
    """
    rows: list[dict] = []
    times: list[pd.Timestamp] = []
    for ts, d in daily.iterrows():
        o, h, l, c, v = (float(d["open"]), float(d["high"]), float(d["low"]),
                         float(d["close"]), float(d["volume"]))
        mid = o + (c - o) * rng.uniform(0.35, 0.65)
        mid = min(max(mid, l), h)                     # منتصف الجلسة داخل نطاق اليوم
        b1 = {"open": o, "close": mid}
        b2 = {"open": mid, "close": c}
        for b in (b1, b2):
            b["low"] = min(b["open"], b["close"])
            b["high"] = max(b["open"], b["close"])
        # فتائل داخل الحدود اليومية
        b1["low"] = max(l, b1["low"] * (1 - abs(rng.normal(0, 0.010))))
        b2["low"] = max(l, b2["low"] * (1 - abs(rng.normal(0, 0.010))))
        b1["high"] = min(h, b1["high"] * (1 + abs(rng.normal(0, 0.010))))
        b2["high"] = min(h, b2["high"] * (1 + abs(rng.normal(0, 0.010))))
        # إجبار التلامس مع أعلى/أدنى اليوم
        (b1 if b1["low"] <= b2["low"] else b2)["low"] = l
        (b1 if b1["high"] >= b2["high"] else b2)["high"] = h
        v1 = max(500.0, v * rng.uniform(0.42, 0.58))
        b1["volume"] = v1
        b2["volume"] = max(500.0, v - v1)
        rows.extend([b1, b2])
        times.extend([ts + pd.Timedelta(hours=13), ts + pd.Timedelta(hours=17)])
    df = pd.DataFrame(rows, index=pd.DatetimeIndex(times))
    df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].round(4)
    df["volume"] = df["volume"].round(0)
    return df


# --------------------------------------------------------------------------- #
#  المزوّد
# --------------------------------------------------------------------------- #
class DemoProvider(BaseProvider):
    name = "demo-generator"
    kind = "demo"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._idx = _session_index(420)
        self._by_symbol = {s.symbol: s for s in SCENARIOS}
        self._daily_cache: dict[str, pd.DataFrame] = {}
        self._h4_cache: dict[str, pd.DataFrame] = {}
        self._extra = self._build_filler_symbols()

    # ---------------- بيانات الأسعار ---------------- #
    def _build_filler_symbols(self) -> list[str]:
        """رموز إضافية متنوعة لتجربة فرز السوق (بعضها بتقسيم عكسي)."""
        return [
            "AXGN", "BIOK", "CDLX", "DRTS", "EFTR", "FLNT", "GNSX", "HRTX", "IMPL",
            "KZIA", "LGMK", "MDIA", "NRXP", "OPGN", "PHUN", "QLGN", "RUBY", "SNGX",
            "TRVN", "VBIV", "WKSP",
        ]

    def _filler_frame(self, symbol: str) -> pd.DataFrame:
        rng = np.random.default_rng(_seed(symbol, "filler"))
        # ~40% من الرموز لديها تقسيم عكسي داخل نافذة الشهرين، والبقية أقدم
        has_rs = bool(rng.random() < 0.42)
        rs_off = -int(rng.integers(6, 56)) if has_rs else -int(rng.integers(90, 260))
        ratio = float(rng.choice([2, 3, 4, 5, 8, 12, 15, 20, 30, 40, 60]))
        pre = float(rng.uniform(0.6, 6.0))
        sc = Scenario(
            symbol=symbol,
            name=f"{symbol} Corp.",
            sector=str(rng.choice(["رعاية صحية", "تقنية", "طاقة", "صناعة", "خدمات"])),
            industry="-",
            exchange=str(rng.choice(["NASDAQ", "NYSE", "NYSE American"])),
            rs_offset=rs_off, rs_ratio=ratio,
            shares_out=float(rng.integers(2, 40)) * 1e6,
            float_shares=float(rng.integers(1, 25)) * 1e6,
            short_pct_float=float(rng.uniform(1, 22)),
            days_to_cover=float(rng.uniform(0.3, 4.5)),
            short_change=float(rng.normal(2, 10)),
            base_volume=float(rng.uniform(1.5e5, 3.0e6)),
            spike_pct=float(rng.uniform(-4, 24)),
            drop_pct=float(rng.uniform(15, 65)),
            bottom_offset=min(-1, max(rs_off + 3, -int(rng.integers(1, 40)))),
            rsi_trough=float(rng.uniform(16, 38)),
            rsi_now_target=float(rng.uniform(28, 62)),
            recovery_strength=float(rng.uniform(0, 1)),
            tail_gain_pct=float(rng.choice([-9.0, -4.0, 3.0, 8.0, 15.0, 24.0], p=[0.16, 0.14, 0.2, 0.22, 0.16, 0.12])),
            lifecycle=bool(rng.random() < 0.75),
            calm_sessions=int(rng.integers(2, 6)),
            impulse_pct=float(rng.choice([8.0, 14.0, 19.0, 22.0, 28.0])),
            impulse_sessions=int(rng.integers(2, 6)),
            pullback_sessions=int(rng.integers(2, 5)),
            pullback_target=str(rng.choice(["sweep", "support"])),
            sweep_depth_pct=float(rng.uniform(2.5, 7.0)),
            reclaim_sessions=int(rng.integers(1, 4)),
            pre_price=pre,
        )
        return _build_daily(sc, self._idx, seed=_seed(symbol, "daily"))

    def _daily(self, symbol: str) -> pd.DataFrame:
        symbol = symbol.upper()
        if symbol in self._daily_cache:
            return self._daily_cache[symbol]
        sc = self._by_symbol.get(symbol)
        df = _build_daily(sc, self._idx) if sc else self._filler_frame(symbol)
        self._daily_cache[symbol] = df
        return df

    def daily(self, symbol: str, lookback_days: int = 260) -> pd.DataFrame:
        df = self._daily(symbol)
        cutoff = self._idx[-1] - pd.Timedelta(days=lookback_days)
        return df.loc[df.index >= cutoff].copy()

    def intraday_4h(self, symbol: str, lookback_days: int = 90) -> pd.DataFrame:
        symbol = symbol.upper()
        if symbol not in self._h4_cache:
            rng = np.random.default_rng(_seed(symbol, "4h"))
            self._h4_cache[symbol] = _build_4h(self._daily(symbol), rng)
        df = self._h4_cache[symbol]
        cutoff = df.index[-1] - pd.Timedelta(days=lookback_days)
        return df.loc[df.index >= cutoff].copy()

    # ---------------- التقسيمات ---------------- #
    def splits(self, symbol: str) -> list[ReverseSplitEvent]:
        symbol = symbol.upper()
        df = self._daily(symbol)
        rs_date = df.attrs.get("rs_date")
        ratio = float(df.attrs.get("rs_ratio") or 0)
        if not rs_date or ratio <= 1:
            return []
        return [
            ReverseSplitEvent(
                symbol=symbol,
                date=pd.Timestamp(rs_date),
                ratio_str=f"1-for-{ratio:g}",
                factor=ratio,
                source="demo-generator",
                url="",
            )
        ]

    # ---------------- الملف التعريفي ---------------- #
    def profile(self, symbol: str) -> TickerProfile:
        symbol = symbol.upper()
        sc = self._by_symbol.get(symbol)
        df = self._daily(symbol)
        last_close = float(df["close"].iloc[-1])
        if sc:
            shares, flt = sc.shares_out, sc.float_shares
            avg_vol = float(df["volume"].tail(20).mean())
            return TickerProfile(
                symbol=symbol, name=sc.name, exchange=sc.exchange, sector=sc.sector,
                industry=sc.industry, shares_out=shares, float_shares=flt,
                market_cap=shares * last_close, avg_volume=avg_vol,
                avg_dollar_volume=avg_vol * last_close,
                insider_pct=round(100 - (flt / shares) * 100, 1),
                institutional_pct=round(min(45.0, 8 + (flt / shares) * 30), 1),
                source="demo-generator", as_of=str(self._idx[-1].date()),
            )
        rng = np.random.default_rng(_seed(symbol, "filler"))
        shares = float(rng.integers(2, 40)) * 1e6
        flt = shares * rng.uniform(0.45, 0.98)
        avg_vol = float(df["volume"].tail(20).mean())
        return TickerProfile(
            symbol=symbol, name=f"{symbol} Corp.", exchange="NASDAQ", sector="خدمات",
            industry="-", shares_out=shares, float_shares=flt,
            market_cap=shares * last_close, avg_volume=avg_vol,
            avg_dollar_volume=avg_vol * last_close,
            insider_pct=round(100 - (flt / shares) * 100, 1),
            institutional_pct=round(rng.uniform(5, 45), 1),
            source="demo-generator", as_of=str(self._idx[-1].date()),
        )

    # ---------------- البيع المكشوف ---------------- #
    def short_data(self, symbol: str) -> ShortData | None:
        symbol = symbol.upper()
        sc = self._by_symbol.get(symbol)
        prof = self.profile(symbol)
        if not sc:
            rng = np.random.default_rng(_seed(symbol, "si"))
            pct = float(rng.uniform(1, 20))
            si = (prof.float_shares or 0) * pct / 100
            return ShortData(
                symbol=symbol, as_of=str((self._idx[-1] - pd.Timedelta(days=15)).date()),
                short_shares=si, short_pct_float=round(pct, 2),
                days_to_cover=round(float(rng.uniform(0.3, 4.0)), 2),
                prev_short_shares=si * (1 + float(rng.normal(0, 0.12))),
                short_volume_ratio=round(float(rng.uniform(0.2, 0.6)), 3),
                source="demo-generator (FINRA-style)",
            )
        si = (prof.float_shares or 0) * sc.short_pct_float / 100
        return ShortData(
            symbol=symbol, as_of=str((self._idx[-1] - pd.Timedelta(days=15)).date()),
            short_shares=si, short_pct_float=round(sc.short_pct_float, 2),
            days_to_cover=round(sc.days_to_cover, 2),
            prev_short_shares=si / (1 + sc.short_change / 100.0) if sc.short_change != -100 else si,
            short_volume_ratio=round(min(0.85, 0.25 + sc.short_pct_float / 100), 3),
            source="demo-generator (FINRA-style)",
        )

    # ---------------- الأخبار والمحفزات ---------------- #
    def _offset_date(self, offset: int) -> str:
        if offset >= 0:
            base = self._idx[-1] + pd.tseries.offsets.BDay(offset) if offset else self._idx[-1]
        else:
            base = _px(self._idx, offset, self._idx[0])
        return str(pd.Timestamp(base).date())

    def catalysts(self, symbol: str) -> list[Catalyst]:
        symbol = symbol.upper()
        sc = self._by_symbol.get(symbol)
        out: list[Catalyst] = []
        if sc:
            for c in sc.catalysts:
                out.append(Catalyst(
                    symbol=symbol, date=self._offset_date(int(c["offset"])),
                    type=str(c["type"]), title=str(c["title"]),
                    sentiment=str(c["sentiment"]), impact=str(c["impact"]),
                    source="demo-generator",
                ))
        else:
            rng = np.random.default_rng(_seed(symbol, "cat"))
            for off, typ, sent in (
                (int(rng.integers(-20, -2)), "earnings", "neutral"),
                (int(rng.integers(3, 35)), "earnings", "neutral"),
                (int(rng.integers(-15, -1)), "offering", "negative"),
            ):
                out.append(Catalyst(
                    symbol=symbol, date=self._offset_date(off), type=typ,
                    title={"earnings": "موعد الإعلان عن النتائج", "offering": "إيداع طرح أسهم"}[typ],
                    sentiment=sent, impact="medium", source="demo-generator",
                ))
        return sorted(out, key=lambda c: c.date)

    def dilution(self, symbol: str) -> list[DilutionSignal]:
        symbol = symbol.upper()
        sc = self._by_symbol.get(symbol)
        if not sc:
            return []
        return [
            DilutionSignal(
                symbol=symbol, kind=str(d["kind"]), severity=str(d["severity"]),
                title=str(d["title"]), date=self._offset_date(int(d["offset"])),
                source="demo-generator (SEC-style)",
            )
            for d in sc.dilution
        ]

    # ---------------- الكون ---------------- #
    def universe(self, limit: int = 100) -> list[str]:
        syms = [s.symbol for s in SCENARIOS] + self._extra
        return syms[:limit] if limit else syms

    def scenario_note(self, symbol: str) -> str:
        sc = self._by_symbol.get(symbol.upper())
        return sc.note_ar if sc else ""


def demo_metadata() -> list[dict]:
    """وصف السيناريوهات لعرضها في الداشبورد (شفافية البيانات التجريبية)."""
    return [
        {
            "symbol": s.symbol, "name": s.name, "rs_ratio": f"1-for-{s.rs_ratio:g}",
            "rs_offset_sessions": s.rs_offset, "spike_pct": s.spike_pct,
            "drop_pct": s.drop_pct, "float": s.float_shares, "si_pct": s.short_pct_float,
            "note": s.note_ar,
        }
        for s in SCENARIOS
    ]
