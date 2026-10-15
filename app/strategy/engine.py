"""محرّك الاستراتيجية — RS Reversal Strategy Engine.

تنفيذ حرفي لشروط ورقة الاستراتيجية:

  1) رصد تقسيم عكسي (Reverse Split) بنسبة 1:2 حتى 1:60 خلال آخر 60 يوماً.
  2) قياس صعود يوم التقسيم (نسبة الصعود من سعر الافتتاح إلى قمة اليوم/الجلسات التالية).
  3) قياس الموجة الهابطة من القمة إلى القاع (≥ 30% ، والمثالي 40-50%+).
  4) رصد التشبع البيعي (RSI < 30 ، والأفضل < 25 أو < 20).
  5) رصد القاع والثبات عليه 3-5 جلسات (مع جفاف الحجم وضيق النطاق).
  6) تأكيد الانعكاس فنيًا: استعادة EMA9، تحسّن MACD، قيعان صاعدة، اختراق القناة الهابطة.
  7) البيانات الأساسية: عدد الأسهم، الفلوت، البيع المكشوف، الأخبار والمحفزات المستقبلية.
  8) تصنيف المرحلة + تقييم 0-100 + مستويات دخول/وقف/أهداف مع نسبة مخاطرة/عائد.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Any

import numpy as np
import pandas as pd

from ..analytics import indicators as ind
from ..analytics import patterns as pat
from ..config import StrategyParams, STRATEGY
from ..providers.base import Catalyst, DilutionSignal, ReverseSplitEvent, ShortData, TickerProfile
from ..providers.hub import DataHub

# --------------------------------------------------------------------------- #
#  المراحل
# --------------------------------------------------------------------------- #
PHASES: dict[int, dict[str, str]] = {
    0: {"key": "REJECTED", "ar": "مستبعد", "color": "#5b6472",
        "desc": "لم يستوفِ شروط الاستراتيجية (راجع أسباب الاستبعاد)."},
    1: {"key": "RS_EVENT", "ar": "المرحلة 1 — تقسيم عكسي مرصود", "color": "#2f81f7",
        "desc": "تم رصد حدث تقسيم عكسي داخل النافذة (60 يوماً)، والمراقبة تبدأ من هنا."},
    2: {"key": "DECLINE", "ar": "المرحلة 2 — موجة هبوط وتشبع بيعي", "color": "#e3b341",
        "desc": "السهم في الموجة الهابطة القوية مع تشبع بيعي (RSI < 30) — لا دخول بعد."},
    3: {"key": "BOTTOM_FORMING", "ar": "المرحلة 3 — قاع يتشكّل", "color": "#d29922",
        "desc": "ظهر قاع ولكن الثبات لم يكتمل (أقل من 3 جلسات) — مراقبة لصيقة."},
    4: {"key": "BASE_CONFIRMED", "ar": "المرحلة 4 — ثبات على القاع (قائمة المتابعة)", "color": "#3fb950",
        "desc": "ثبات 3-5 جلسات على القاع مع جفاف الحجم — السهم انتقل لقائمة المتابعة فعلياً."},
    5: {"key": "OVERSOLD_RECOVERY", "ar": "المرحلة 5 — المرحلة الإيجابية (التسلسل قيد التكوّن)", "color": "#2ea043",
        "desc": "RSI يتحسّن وبدأت دورة الحياة: انطلاقة ≈20% و/أو اختبار مقاومة قريبة و/أو عودة لاختبار الدعم — لم تكتمل بعد."},
    6: {"key": "REVERSAL_CONFIRMED", "ar": "المرحلة 6 — جاهز فنياً (اكتمال التسلسل)", "color": "#1f9a4d",
        "desc": "ثبات الدعم ← انطلاقة ≈20% ← اختبار مقاومة قريبة ← عودة لاختبار الدعم أو سحب سيولة تحته ثم استعادته" + " → سهم جاهز فنياً."},
    7: {"key": "EXTENDED", "ar": "مرحلة التمدد — بعد الاختراق", "color": "#8957e5",
        "desc": "السهم ابتعد عن منطقة الارتكاز — الدخول يحتاج انتظار إعادة اختبار."},
}


def phase_info(pid: int) -> dict[str, str]:
    return PHASES.get(pid, PHASES[0])


# مسار GAM ثانوي لعرض حالات الـ near-miss فقط. لا يغيّر شروط الأهلية الصارمة
# ولا قائمة المتابعة، ولا يمثل توصية. رفض RSI هنا ناتج حصراً عن سلسلة الأسعار
# التي يحللها المسح الحالي؛ لا توجد حالة تاريخية محفوظة بين المسوح.
GAM_ALLOWED_REJECTION_CODES = frozenset({"decline", "rsi"})
GAM_RSI_SCOPE = "current_scan_price_series"
GAM_DISCLAIMER_AR = (
    "مرشح GAM للمراجعة فقط — غير مؤهل وفق الشروط الصارمة، وليس فرصة أو توصية. "
    "تقييم RSI مبني على بيانات السعر في المسح الحالي ولا تُحفظ حالات رفض تاريخية بين المسوح."
)


# --------------------------------------------------------------------------- #
#  بنية النتيجة
# --------------------------------------------------------------------------- #
@dataclass
class ScorePart:
    key: str
    ar: str
    score: float           # 0..100
    weight: float
    detail: str = ""

    @property
    def points(self) -> float:
        return self.score * self.weight / 100.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["points"] = round(self.points, 2)
        d["score"] = round(self.score, 1)
        return d


@dataclass
class Analysis:
    symbol: str
    name: str = ""
    sector: str = ""
    industry: str = ""
    exchange: str = ""
    data_source: str = "demo"
    price_source: str = ""
    split_source: str = ""
    fundamentals_source: str = ""
    news_source: str = ""
    synthetic: bool = True   # True = بيانات مولّدة/تجريبية، False = حقيقية مؤكدة
    as_of: str = ""
    last_close: float = 0.0
    change_pct: float = 0.0
    dollar_volume: float = 0.0

    # -- حدث التقسيم -- #
    rs_found: bool = False
    rs_date: str | None = None
    rs_age_days: int | None = None
    rs_ratio_str: str = ""
    rs_ratio: float = 0.0
    rs_source: str = ""
    rs_url: str = ""
    rs_open: float | None = None
    spike_high: float | None = None
    spike_pct: float | None = None
    spike_date: str | None = None

    # -- الهبوط والقاع -- #
    decline_pct: float | None = None
    peak_date: str | None = None
    bottom_price: float | None = None
    bottom_date: str | None = None
    base_sessions: int = 0
    base_high: float | None = None
    base_low: float | None = None
    base_range_pct: float | None = None
    base_broken: bool = False
    volume_dryup: float | None = None

    # -- التشبع والانعكاس -- #
    atr_pct: float | None = None
    rsi_now: float | None = None
    rsi_trough: float | None = None
    rsi_trough_date: str | None = None
    rsi_sessions_below_30: int = 0
    rsi_recovered: bool = False
    above_ema9_sessions: int = 0
    macd_turning: bool = False
    macd_hist: float | None = None
    channel_slope_pct: float | None = None
    channel_upper: float | None = None
    channel_broken: bool = False
    rvol: float | None = None
    higher_lows: int = 0
    squeeze: bool = False

    # -- دورة الحياة بعد الثبات (شرط «جاهز فنياً») -- #
    lifecycle_stage: str = ""
    lifecycle_ready: bool = False
    lifecycle_failed: bool = False
    lifecycle_checks: list[dict] = field(default_factory=list)
    impulse_pct: float | None = None
    impulse_high: float | None = None
    impulse_date: str | None = None
    resistance_level: float | None = None
    resistance_distance_pct: float | None = None
    resistance_date: str | None = None
    pullback_type: str | None = None
    pullback_low: float | None = None
    pullback_date: str | None = None
    sweep_low: float | None = None
    sweep_depth_pct: float | None = None
    sweep_date: str | None = None
    reclaim_level: float | None = None
    reclaim_sessions: int = 0
    reclaim_date: str | None = None
    support_level: float | None = None
    support_hold_ok: bool = False
    base_close: float | None = None
    lifecycle_sequence_ok: bool = False
    resistance_kind: str | None = None
    spark: list[float] = field(default_factory=list)      # آخر 40 إغلاقاً للرسم المصغّر
    resistance_tested: bool = False
    resistance_stall_pct: float | None = None

    # -- الأساسيات -- #
    shares_out: float | None = None
    float_shares: float | None = None
    market_cap: float | None = None
    avg_volume: float | None = None
    insider_pct: float | None = None
    institutional_pct: float | None = None
    short_pct_float: float | None = None
    short_shares: float | None = None
    days_to_cover: float | None = None
    short_change_pct: float | None = None
    short_volume_ratio: float | None = None
    short_source: str = ""

    # -- الأخبار والمخاطر -- #
    catalysts: list[dict] = field(default_factory=list)
    dilution: list[dict] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    positive_news: list[dict] = field(default_factory=list)
    negative_news: list[dict] = field(default_factory=list)

    # -- المرحلة والتقييم -- #
    phase: int = 0
    display_phase: int = 0
    phase_key: str = "REJECTED"
    phase_ar: str = ""
    eligible: bool = False
    watchlist: bool = False
    # GAM مسار عرض ثانوي للرفض القريب؛ يبقى دائماً منفصلاً عن الأهلية والمتابعة.
    gam_candidate: bool = False
    gam_reasons: list[str] = field(default_factory=list)
    gam_rsi_scope: str = GAM_RSI_SCOPE
    rejection_codes: list[str] = field(default_factory=list)
    rejections: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    score: float = 0.0
    grade: str = "—"
    breakdown: list[dict] = field(default_factory=list)
    confirmations: list[str] = field(default_factory=list)

    # -- المستويات الفنية -- #
    levels: dict[str, Any] = field(default_factory=dict)

    # -- النماذج الفنية -- #
    patterns_daily: list[dict] = field(default_factory=list)
    patterns_4h: list[dict] = field(default_factory=list)

    # -- فريم 4 ساعات -- #
    h4: dict[str, Any] = field(default_factory=dict)

    # -- السرد التحليلي -- #
    why_ar: list[str] = field(default_factory=list)
    action_ar: str = ""
    note_ar: str = ""

    # -- سلسلة السعر (للرسوم) -- #
    series: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self, with_series: bool = False) -> dict[str, Any]:
        d = asdict(self)
        info = phase_info(self.phase if (self.eligible or not self.rs_found) else 0)
        d["phase_info"] = info
        d["phase_key"] = info["key"]
        d["phase_ar"] = info["ar"]
        d["phase_color"] = info["color"]
        if not with_series:
            d.pop("series", None)
        return d


# --------------------------------------------------------------------------- #
#  أدوات مساعدة
# --------------------------------------------------------------------------- #
def _r(x, n=4):
    try:
        if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
            return None
        return round(float(x), n)
    except (TypeError, ValueError):
        return None


def _pct(a, b) -> float | None:
    try:
        if b in (None, 0):
            return None
        return (float(a) / float(b) - 1.0) * 100.0
    except (TypeError, ValueError):
        return None


def _date_str(x) -> str | None:
    try:
        return str(pd.Timestamp(x).date())
    except Exception:  # noqa: BLE001
        return None


def _scaled(value: float, points: list[tuple[float, float]]) -> float:
    """تنقيط خطي بين نقاط (قيمة, درجة) مع تثبيت الأطراف."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return 0.0
    pts = sorted(points)
    if value <= pts[0][0]:
        return pts[0][1]
    if value >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= value <= x1:
            if x1 == x0:
                return y1
            return y0 + (y1 - y0) * (value - x0) / (x1 - x0)
    return pts[-1][1]


def add_rejection(a: Analysis, code: str, reason: str) -> None:
    """يسجّل رفضاً منظماً مع النص العربي المعروض للمستخدم.

    الربط بين كل سبب ورمزه يمنع GAM من الاعتماد على مطابقة نصوص قابلة للتغيّر،
    كما يجعل أي سبب غير مصنّف مانعاً افتراضياً بدلاً من تمريره خطأً.
    """
    a.rejection_codes.append(str(code))
    a.rejections.append(str(reason))


def evaluate_gam_candidate(a: Analysis, params: StrategyParams = STRATEGY) -> bool:
    """يحدّد مسار GAM دون المساس بالأهلية الصارمة أو قائمة المتابعة.

    يقبل GAM فقط سهماً له تقسيم عكسي حديث صالح وسعر ضمن النطاق، وكانت كل
    أسباب رفضه الصارم محصورة في هبوط أقل من الحد و/أو عدم نزول RSI تحت الحد.
    نقص أي قياس أساسي، ضعف السيولة، فشل الصعود، كسر القاع، أو أي رفض آخر
    يمنع GAM. بيانات RSI تخص سلسلة السعر في المسح الحالي فقط؛ لا تخزين تاريخي.
    """
    a.gam_candidate = False
    a.gam_reasons = []

    # GAM حالة رفض قريب، وليس اسماً بديلاً لفرصة مؤهلة.
    if a.eligible or not a.rs_found or not a.rejections:
        return False
    if not (params.min_price <= (a.last_close or 0) <= params.max_price):
        return False

    # كل رفض يجب أن يحمل رمزاً؛ أي سبب جديد/غير مصنف يُعامل كمانع آمن.
    if len(a.rejection_codes) != len(a.rejections):
        return False
    codes = set(a.rejection_codes)
    if not codes or not codes.issubset(GAM_ALLOWED_REJECTION_CODES):
        return False

    # منع نقص البيانات حتى لو تغيّر منطق الرفض مستقبلاً، والتحقق من أن الرفض
    # يعكس قراءات المسح الحالي فعلاً (لا رمز RSI قديم أو حالة مخزنة).
    if a.decline_pct is None or a.rsi_trough is None or a.spike_pct is None:
        return False
    current_failures: set[str] = set()
    if abs(a.decline_pct) < params.min_drop_pct:
        current_failures.add("decline")
    if a.rsi_trough >= params.rsi_oversold:
        current_failures.add("rsi")
    if codes != current_failures:
        return False
    if a.spike_pct < params.min_spike_pct:
        return False
    if (a.dollar_volume or 0) < params.min_avg_dollar_volume:
        return False
    if params.max_float_shares and (a.float_shares or 0) > params.max_float_shares:
        return False
    if a.base_broken or a.lifecycle_failed:
        return False

    a.gam_candidate = True
    a.gam_reasons = [
        reason for code, reason in zip(a.rejection_codes, a.rejections)
        if code in GAM_ALLOWED_REJECTION_CODES
    ]
    return True


# --------------------------------------------------------------------------- #
#  كشف التقسيم العكسي
# --------------------------------------------------------------------------- #
def pick_reverse_split(events: list[ReverseSplitEvent], params: StrategyParams,
                       as_of: pd.Timestamp | None = None) -> tuple[ReverseSplitEvent | None, list[str]]:
    """اختيار التقسيم العكسي الصالح داخل النافذة + أسباب الرفض."""
    if not events:
        return None, ["لا يوجد حدث تقسيم عكسي مسجّل لهذا الرمز"]
    as_of = as_of or pd.Timestamp.now().normalize()
    reasons: list[str] = []
    best: ReverseSplitEvent | None = None
    for ev in sorted(events, key=lambda e: e.date, reverse=True):
        age = int((as_of.normalize() - pd.Timestamp(ev.date).normalize()).days)
        if age < 0:
            continue
        if age > params.rs_lookback_days:
            reasons.append(
                f"تقسيم عكسي عمره {age} يوماً ({ev.ratio_str}) — خارج نافذة "
                f"{params.rs_lookback_days} يوماً المطلوبة"
            )
            continue
        if not (params.rs_min_ratio <= float(ev.factor) <= params.rs_max_ratio):
            reasons.append(
                f"نسبة التقسيم {ev.ratio_str} خارج النطاق المقبول "
                f"1:{params.rs_min_ratio:g} – 1:{params.rs_max_ratio:g}"
            )
            continue
        if best is None:
            best = ev          # الأحدث الصالح (القائمة مرتبة تنازلياً)
    return best, reasons


# --------------------------------------------------------------------------- #
#  التحليل البنيوي (صعود التقسيم، الهبوط، القاع، الثبات)
# --------------------------------------------------------------------------- #
def analyse_structure(df: pd.DataFrame, ev: ReverseSplitEvent, params: StrategyParams) -> dict[str, Any]:
    """تحليل البنية السعرية بعد حدث التقسيم العكسي."""
    out: dict[str, Any] = {}
    rs_ts = pd.Timestamp(ev.date).normalize()
    # أول جلسة تداول عند/بعد تاريخ التقسيم
    after = df.index[df.index >= rs_ts]
    if len(after) == 0:
        after = df.index[-1:]
    rs_pos = int(df.index.get_loc(after[0]))
    rs_bar = df.iloc[rs_pos]
    rs_open = float(rs_bar["open"]) or float(rs_bar["close"])

    # (2) قمة ما بعد التقسيم خلال نافذة الصعود
    end_pos = min(len(df), rs_pos + max(1, params.spike_window_sessions) + 1)
    seg = df.iloc[rs_pos:end_pos]
    peak_pos = int(seg["high"].to_numpy().argmax()) + rs_pos
    spike_high = float(df["high"].iloc[peak_pos])
    spike_pct = _pct(spike_high, rs_open)
    out.update(
        rs_open=_r(rs_open), spike_high=_r(spike_high), spike_pct=_r(spike_pct),
        spike_date=_date_str(df.index[peak_pos]), peak_pos=peak_pos, rs_pos=rs_pos,
    )

    # (3) القاع المرجعي بعد القمة + الموجة الهابطة
    #     نحدّد القاع المرجعي بأنه أدنى قاع سُجّل قبيل «الانطلاقة» (ارتفاع ≥ الحد الأدنى للانطلاقة
    #     من أدنى قاع مسجّل) — حتى لا يلتقط النظام «سحب السيولة» اللاحق كقاع مرجعي.
    #     وإذا لم توجد انطلاقة (هبوط مستمر/قاعدة مسطحة) نرجع للتعريف البسيط: أدنى قاع بعد القمة.
    # القاع المرجعي = أدنى قاع سُجّل **قبل** الانطلاقة، حيث تُعرَّف الانطلاقة بأنها ارتفاع
    # لاحق ≥ الحد الأدنى المطلوب (15%) من ذلك القاع. هذا يستثني «سحب السيولة» اللاحق
    # (فتيل تحت الدعم) لأن ما يتبعه من ارتفاع لا يبلغ حد الانطلاقة.
    seg = df.iloc[peak_pos:]
    lows_arr = seg["low"].to_numpy(dtype=float)
    highs_arr = seg["high"].to_numpy(dtype=float)
    n_seg = len(lows_arr)
    if n_seg:
        suffix_max = np.full(n_seg, -np.inf)
        for k in range(n_seg - 2, -1, -1):
            suffix_max[k] = max(suffix_max[k + 1], highs_arr[k + 1])
        target_mult = 1 + params.min_ready_impulse_pct / 100.0
        qualifying = np.where((lows_arr > 0) & (suffix_max >= lows_arr * target_mult))[0]
        if qualifying.size:
            bottom_pos = peak_pos + int(qualifying[np.argmin(lows_arr[qualifying])])
        else:   # لا توجد انطلاقة ⇒ نعود للتعريف البسيط: أدنى قاع بعد القمة
            bottom_pos = peak_pos + int(lows_arr.argmin())
    else:
        bottom_pos = peak_pos
    bottom_price = float(df["low"].iloc[bottom_pos])
    decline_pct = _pct(bottom_price, spike_high)
    out.update(
        bottom_pos=bottom_pos, bottom_price=_r(bottom_price), bottom_date=_date_str(df.index[bottom_pos]),
        peak_date=_date_str(df.index[peak_pos]), decline_pct=_r(decline_pct),
    )

    # (5) الثبات على القاع
    hard_break = bottom_price * (1 - params.break_low_tolerance_pct / 100.0)
    band = bottom_price * (1 + params.base_tolerance_pct / 100.0)      # حد القاعدة السعري
    band_hi = bottom_price * (1 + params.base_tolerance_pct / 100.0 * 1.05)  # بداية الاختراق
    sessions = 0
    broken = False
    escape_pos = None
    for i in range(bottom_pos + 1, len(df)):
        low = float(df["low"].iloc[i])
        high = float(df["high"].iloc[i])
        if low < hard_break:
            broken = True
            break
        if low <= band and high <= band_hi:
            sessions += 1               # داخل منطقة القاعدة
        else:
            sessions += 1               # جلسة الاختراق/الخروج — تُحتسب ثم تتوقف القاعدة
            escape_pos = i
            break
    win_lo = bottom_pos + 1
    win_hi = escape_pos if escape_pos is not None else (bottom_pos + sessions)
    win_hi = max(win_hi, win_lo)
    base_window = df.iloc[win_lo : win_hi + 1]
    base_high = float(base_window["high"].max()) if len(base_window) else bottom_price
    base_range = (base_high - bottom_price) / bottom_price * 100 if bottom_price else None

    # جفاف الحجم = متوسط حجم جلسات القاعدة مقارنةً بمتوسط 20 جلسة سبقت القاع
    bw = base_window["volume"] if len(base_window) else df["volume"].iloc[-3:]
    vol_base = float(bw.mean()) if len(bw) else 0.0
    ref_start = max(0, bottom_pos - 20)
    vol_ref = float(df["volume"].iloc[ref_start:bottom_pos].mean()) if bottom_pos > ref_start else 0.0
    dryup = (vol_base / vol_ref) if vol_ref else None

    # نهاية القاعدة: شمعة الاختراق تُحتسب بداية الانطلاقة (خارج القاعدة)
    if escape_pos is not None:
        base_end_pos = max(bottom_pos, escape_pos - 1)
    else:
        base_end_pos = max(bottom_pos, bottom_pos + max(0, sessions - 1))
    base_end_pos = min(base_end_pos, len(df) - 1)
    base_close = float(df["close"].iloc[base_end_pos])
    base_low = float(df["low"].iloc[bottom_pos : base_end_pos + 1].min()) if base_end_pos >= bottom_pos else bottom_price

    out.update(
        base_sessions=int(sessions), base_broken=bool(broken), base_high=_r(base_high),
        base_vol_ratio=_r(dryup, 3),
        base_range_pct=_r(base_range), volume_dryup=_r(dryup, 3),
        base_low=_r(bottom_price),
        base_end_pos=int(base_end_pos), base_close=_r(base_close), support=_r(base_low),
    )
    return out


# --------------------------------------------------------------------------- #
#  التشبع والانعكاس
# --------------------------------------------------------------------------- #
def analyse_momentum(df: pd.DataFrame, struct: dict, params: StrategyParams) -> dict[str, Any]:
    out: dict[str, Any] = {}
    rsi = df["rsi"]
    peak_pos = struct["peak_pos"]
    bottom_pos = struct["bottom_pos"]

    seg = rsi.iloc[peak_pos:]
    if seg.dropna().empty:
        out.update(rsi_now=_r(rsi.iloc[-1], 2), rsi_trough=None)
    else:
        trough_pos = int(seg.fillna(100).to_numpy().argmin()) + peak_pos
        out.update(
            rsi_now=_r(rsi.iloc[-1], 2),
            rsi_trough=_r(float(rsi.iloc[trough_pos]), 2),
            rsi_trough_date=_date_str(df.index[trough_pos]),
        )
    below = int((rsi.iloc[peak_pos:] < params.rsi_oversold).sum())
    out["rsi_sessions_below_30"] = below
    trough = out.get("rsi_trough")
    now = out.get("rsi_now")
    recovered = bool(
        trough is not None and now is not None
        and now >= min(params.rsi_recovery_min, trough + params.rsi_recovery_delta)
        and now > trough
    )
    out["rsi_recovered"] = recovered

    # استعادة المتوسطات
    above = 0
    for i in range(len(df) - 1, max(-1, len(df) - 15), -1):
        if float(df["close"].iloc[i]) > float(df["ema9"].iloc[i]):
            above += 1
        else:
            break
    out["above_ema9_sessions"] = above
    out["ema9"] = _r(df["ema9"].iloc[-1])
    out["ema20"] = _r(df["ema20"].iloc[-1])
    out["ema50"] = _r(df["ema50"].iloc[-1])
    out["above_ema20"] = bool(float(df["close"].iloc[-1]) > float(df["ema20"].iloc[-1]))

    # MACD
    hist = df["macd_hist"]
    if len(hist) >= 4 and hist.notna().tail(4).all():
        h = [float(x) for x in hist.tail(4)]
        out["macd_turning"] = bool(h[-1] > h[-2] or (h[-1] > h[-3] and h[-1] < 0))
        out["macd_above_signal"] = bool(float(df["macd"].iloc[-1]) > float(df["macd_signal"].iloc[-1]))
    else:
        out["macd_turning"] = False
        out["macd_above_signal"] = False
    out["macd_hist"] = _r(hist.iloc[-1], 5)

    # القناة الهابطة
    close = float(df["close"].iloc[-1])
    ch_up = df["ch_up"].iloc[-1] if "ch_up" in df else np.nan
    ch_slope = df["ch_slope_pct"].iloc[-1] if "ch_slope_pct" in df else np.nan
    out["channel_upper"] = _r(ch_up)
    out["channel_slope_pct"] = _r(ch_slope, 3)
    out["channel_broken"] = bool(pd.notna(ch_up) and close > float(ch_up))
    out["channel_down"] = bool(pd.notna(ch_slope) and float(ch_slope) < -0.05)

    # الحجم
    out["rvol"] = _r(df["rvol"].iloc[-1], 2) if "rvol" in df else None
    out["dollar_vol"] = _r(df["dollar_vol"].iloc[-1], 0) if "dollar_vol" in df else None
    out["squeeze"] = bool(df["sqz"].iloc[-1]) if "sqz" in df else False
    out["atr_pct"] = _r(df["atr_pct"].iloc[-1], 2) if "atr_pct" in df else None

    # بنية القيعان
    try:
        since = pd.Timestamp(struct.get("bottom_date")) if struct.get("bottom_date") else df.index[-15]
    except Exception:  # noqa: BLE001
        since = df.index[-15]
    out["higher_lows"] = ind.count_higher_lows(df, since)
    out["bullish_divergence"] = bool(ind.bullish_divergence(df, rsi, lookback=45))
    return out


def quick_momentum(df: pd.DataFrame, params: StrategyParams) -> dict[str, Any]:
    """مؤشرات سريعة لأي رمز (تُستخدم عند عدم وجود حدث تقسيم عكسي)."""
    out: dict[str, Any] = {}
    try:
        out["rsi_now"] = _r(df["rsi"].iloc[-1], 2)
        out["rsi_trough"] = _r(df["rsi"].tail(40).min(), 2)
        out["atr_pct"] = _r(df["atr_pct"].iloc[-1], 2)
        out["rvol"] = _r(df["rvol"].iloc[-1], 2)
        out["ema9"] = _r(df["ema9"].iloc[-1])
        out["ema20"] = _r(df["ema20"].iloc[-1])
        out["ema50"] = _r(df["ema50"].iloc[-1])
        out["macd_turning"] = bool(
            df["macd_hist"].notna().tail(3).all()
            and float(df["macd_hist"].iloc[-1]) > float(df["macd_hist"].iloc[-2]))
        above = 0
        for i in range(len(df) - 1, max(-1, len(df) - 15), -1):
            if float(df["close"].iloc[i]) > float(df["ema9"].iloc[i]):
                above += 1
            else:
                break
        out["above_ema9_sessions"] = above
        out["channel_upper"] = _r(df["ch_up"].iloc[-1]) if "ch_up" in df else None
        out["channel_broken"] = bool(
            "ch_up" in df and pd.notna(df["ch_up"].iloc[-1])
            and float(df["close"].iloc[-1]) > float(df["ch_up"].iloc[-1]))
        out["squeeze"] = bool(df["sqz"].iloc[-1]) if "sqz" in df else False
        out["higher_lows"] = ind.count_higher_lows(df, df.index[-25])
    except Exception:  # noqa: BLE001
        pass
    return out


def analyse_lifecycle(df: pd.DataFrame, struct: dict, params: StrategyParams) -> dict[str, Any]:
    """تحليل دورة الحياة بعد الثبات على الدعم (شروط «جاهز فنياً» في ورقة الشروط):

    1) انطلاقة ≈ 20% (أقل/أكثر قليلاً) من إغلاق الثبات
    2) اختبار مقاومة قريبة (أو وجود مقاومة قريبة فوق الانطلاقة)
    3) العودة لاختبار الدعم الرئيسي **أو** سحب سيولة تحت الدعم
    4) استعادة/ثبات فوق الدعم بعد السحب
    """
    out: dict[str, Any] = {
        "stage": "لا يوجد تسلسل بعد", "ready": False, "failed": False,
        "impulse_pct": None, "impulse_high": None, "impulse_low": None, "impulse_date": None,
        "resistance_level": None, "resistance_distance_pct": None, "resistance_date": None,
        "pullback_type": None, "pullback_low": None, "pullback_date": None,
        "sweep_low": None, "sweep_depth_pct": None, "sweep_date": None,
        "reclaim_level": None, "reclaim_sessions": 0, "reclaim_date": None,
        "return_to_support": False, "reclaimed": False, "checks": [],
        "support_hold_ok": False, "base_sessions": 0,
        "resistance_kind": None, "sequence_ok": False,
        "resistance_stall_pct": None, "resistance_tested": False,
    }
    base_end = struct.get("base_end_pos")
    support = float(struct.get("support") or struct.get("bottom_price") or 0)
    base_close = struct.get("base_close")
    base_high = float(struct.get("base_high") or 0)
    if base_end is None or not support or base_close is None:
        return out
    if int(base_end) >= len(df) - 1:
        out["stage"] = "بانتظار جلسات ما بعد الثبات"
        return out

    post = df.iloc[int(base_end) + 1 :]
    peak_pos = int(post["high"].to_numpy().argmax())
    impulse_high = float(post["high"].iloc[peak_pos])
    impulse_low = float(post["low"].iloc[: peak_pos + 1].min())
    impulse_pct = _pct(impulse_high, base_close)
    impulse_date = _date_str(post.index[peak_pos])

    # ---- أقرب مقاومة: أعلى قمة محورية فشل عندها السعر قبل قمة الانطلاقة ---- #
    #      (قمم محورية = أعلى سعر في نافذة ±2 جلسات، وهي «مقاومات» فعلية على المخطط)
    pre = post.iloc[:peak_pos] if peak_pos >= 1 else post.iloc[:1]
    highs_pre = pre["high"].to_numpy(dtype=float)
    pivots: list[tuple[int, float]] = []
    for i in range(len(highs_pre)):
        lo, hi = max(0, i - 2), min(len(highs_pre), i + 3)
        if highs_pre[i] >= highs_pre[lo:hi].max():
            pivots.append((i, float(highs_pre[i])))
    ceiling = impulse_high * 1.02          # المقاومة التي اختُبرت فعلاً (عند القمة أو تحتها)
    floor_level = float(base_close) * 1.02  # ويجب أن تكون فوق إغلاق الثبات
    tested = [(i, lv) for i, lv in pivots if floor_level <= lv <= ceiling]
    if tested:
        r_i, resistance_level = max(tested, key=lambda x: x[1])
        resistance_date = _date_str(pre.index[r_i])
        resistance_kind = "pivot"
    elif peak_pos >= 1:
        resistance_level = float(post["high"].iloc[:peak_pos].max())
        resistance_date = _date_str(post["high"].iloc[:peak_pos].idxmax())
        resistance_kind = "swing"
    else:
        resistance_level = base_high or None
        resistance_date = struct.get("bottom_date")
        resistance_kind = "base"
    resistance_distance = _pct(resistance_level, support) if resistance_level else None

    # ---- الشرط الأول: الثبات على الدعم لعدد جلسات لا يقل عن الحد الأدنى ---- #
    base_sessions = int(struct.get("base_sessions") or 0)
    base_broken = bool(struct.get("base_broken"))
    support_hold_ok = bool(base_sessions >= params.min_base_sessions and not base_broken)

    impulse_ok = bool(impulse_pct is not None and impulse_pct >= params.min_ready_impulse_pct)
    # «مقاومة قريبة» = قريبة من قمة الانطلاقة (≤10% فوقها) وفي الوقت نفسه ليست بعيدة عن الدعم
    resistance_near_impulse = bool(
        resistance_level is not None and resistance_level <= impulse_high * 1.10
    )
    # يجب أن يكون السعر قد «توقف» عند المقاومة (لا تجاوزها بقوة) حتى تُعدّ مقاومة فعلية
    stall_pct = (abs(impulse_high - resistance_level) / resistance_level * 100
                 if resistance_level else None)
    resistance_stalled = bool(stall_pct is not None and stall_pct <= params.resistance_stall_pct)
    resistance_ok = bool(
        resistance_level is not None
        and resistance_distance is not None
        and 0 <= resistance_distance <= params.max_nearby_resistance_pct
        and resistance_near_impulse
        and resistance_stalled
    )

    # ---- العودة لاختبار الدعم / سحب السيولة ---- #
    after = post.iloc[peak_pos + 1 :]
    pullback_type = None
    pullback_low = None
    pullback_date = None
    sweep_low = None
    sweep_depth = None
    sweep_date = None
    reclaim_level = support * (1 + params.reclaim_buffer_pct / 100.0)
    reclaim_sessions = 0
    reclaim_date = None
    return_to_support = False
    reclaimed = False
    failed = False

    if len(after) > 0:
        low_pos = int(after["low"].to_numpy().argmin())
        pullback_low = float(after["low"].iloc[low_pos])
        pullback_date = _date_str(after.index[low_pos])
        sweep_threshold = support * (1 - params.sweep_tolerance_pct / 100.0)
        retest_band = support * (1 + params.support_retest_tolerance_pct / 100.0)
        tail = after.iloc[low_pos:]
        closes_after = tail["close"]
        above = closes_after[closes_after > reclaim_level]
        reclaim_sessions = int(len(above))
        if reclaim_sessions:
            reclaim_date = _date_str(above.index[-1])
        reclaimed = bool(reclaim_sessions >= params.min_reclaim_sessions)

        if pullback_low < sweep_threshold:
            sweep_low = pullback_low
            sweep_depth = abs(_pct(pullback_low, support))   # عمق السحب تحت الدعم (موجب)
            sweep_date = pullback_date
            pullback_type = "liquidity_sweep" if reclaimed else "breakdown"
            return_to_support = True          # السحب يشمل العودة لمنطقة الدعم
            # «الفشل» يُحتسب فقط إذا حدثت انطلاقة حقيقية ثم كسر الدعم دون استعادة
            failed = bool(impulse_ok and not reclaimed)
        elif pullback_low <= retest_band:
            pullback_type = "support_retest"
            return_to_support = True
            failed = bool(impulse_ok and (closes_after < support * (1 - params.sweep_tolerance_pct / 100.0)).any())
        else:
            pullback_type = "shallow_pullback"
            return_to_support = False

    # ---- الحالة والمرحلة ---- #
    sequence_ok = bool(
        impulse_ok and resistance_ok and return_to_support
        and (reclaimed or pullback_type == "support_retest") and not failed
    )
    if failed:
        stage = "فشل — كسر الدعم دون استعادة (إلغاء السيناريو)"
    elif not support_hold_ok and impulse_pct is None:
        stage = (f"بانتظار ثبات الدعم {params.min_base_sessions} جلسات تداول "
                 f"(الحالي {sessions_ar(base_sessions)})")
    elif impulse_pct is None:
        stage = "لا يوجد تسلسل بعد"
    elif not impulse_ok:
        stage = f"انطلاقة ضعيفة (+{impulse_pct:.0f}% — المستهدف ≈{params.ideal_impulse_pct:.0f}%)"
    elif not resistance_ok:
        if resistance_level is None:
            stage = "لم تتشكّل مقاومة قريبة فوق الانطلاقة بعد"
        elif stall_pct is not None and stall_pct > params.resistance_stall_pct:
            stage = (f"تجاوز المقاومة بقوة ({stall_pct:.1f}% فوقها) دون التوقف عندها — "
                     f"بانتظار مقاومة جديدة")
        else:
            stage = f"المقاومة المختبرة بعيدة عن الدعم (أكثر من {params.max_nearby_resistance_pct:.0f}%)"
    elif pullback_type is None:
        stage = "اختبار المقاومة القريبة (بانتظار العودة لاختبار الدعم)"
    elif pullback_type == "shallow_pullback":
        stage = "لم يعد بعد لاختبار الدعم الرئيسي"
    elif pullback_type == "liquidity_sweep" and not reclaimed:
        stage = "سحب سيولة تحت الدعم (بانتظار إغلاق الاستعادة فوقه)"
    elif not reclaimed:
        stage = "عودة لاختبار الدعم الرئيسي (بانتظار إغلاق الاستعادة)"
    elif not support_hold_ok:
        stage = (f"التسلسل مكتمل لكن الثبات على الدعم {sessions_ar(base_sessions)} فقط — "
                 f"الحد الأدنى {params.min_base_sessions} جلسات تداول")
    elif pullback_type == "liquidity_sweep":
        stage = "مكتمل: ثبات → انطلاقة → مقاومة → سحب سيولة → استعادة فوق الدعم"
    else:
        stage = "مكتمل: ثبات → انطلاقة → مقاومة → إعادة اختبار الدعم → استعادة"

    ready = bool(sequence_ok and support_hold_ok)

    checks = [
        {
            "key": "support_hold",
            "ar": f"ثبات على الدعم (أقلها {params.min_base_sessions} جلسات تداول)",
            "std": f"≥ {params.min_base_sessions} جلسات داخل ±{params.base_tolerance_pct:.0f}% حول الدعم دون كسره",
            "ok": support_hold_ok,
            "detail": (f"{sessions_ar(base_sessions)} ثبات على الدعم {support:.3f}"
                       + (" — القاع مكسور ⚠" if base_broken else "")),
        },
        {
            "key": "impulse", "ar": "الانطلاقة بعد الثبات (≈20%)",
            "std": f"≥ {params.min_ready_impulse_pct:.0f}% (المثالي {params.ideal_impulse_pct:.0f}%)",
            "ok": impulse_ok,
            "detail": (f"+{impulse_pct:.1f}% من {base_close:.3f} إلى {impulse_high:.3f}"
                       if impulse_pct is not None else "لم تبدأ بعد"),
        },
        {
            "key": "resistance", "ar": "اختبار مقاومة قريبة",
            "std": f"مقاومة ≤ {params.max_nearby_resistance_pct:.0f}% فوق الدعم",
            "ok": resistance_ok,
            "detail": (f"أقرب مقاومة {resistance_level:.3f} (على {resistance_distance:.1f}% من الدعم {support:.3f}) — "
                       f"توقف السعر عندها بفارق {stall_pct:.1f}%"
                       if resistance_level and resistance_distance is not None and stall_pct is not None
                       else "لا توجد مقاومة محددة"),
        },
        {
            "key": "pullback", "ar": "العودة لاختبار الدعم الرئيسي",
            "std": f"ملامسة الدعم (تسامح {params.support_retest_tolerance_pct:.0f}%)",
            "ok": return_to_support,
            "detail": (f"أدنى نقطة بعد القمة {pullback_low:.3f} مقابل الدعم {support:.3f}"
                       if pullback_low is not None else "لم تحدث موجة عائدة بعد"),
        },
        {
            "key": "sweep", "ar": "سحب سيولة تحت الدعم ثم العودة فوقه (أو إعادة اختبار الدعم)",
            "std": f"كسر الدعم ≥ {params.sweep_tolerance_pct:.0f}% ثم إغلاق فوقه",
            "ok": bool(pullback_type in ("liquidity_sweep", "support_retest") and reclaimed),
            "detail": (f"سحب إلى {sweep_low:.3f} ({sweep_depth:.1f}% تحت الدعم) ثم استعادة"
                       if pullback_type == "liquidity_sweep" and sweep_low
                       else ("إعادة اختبار الدعم دون كسر — استعادة مباشرة" if pullback_type == "support_retest"
                             else ("كسر الدعم دون استعادة ⚠" if failed else "لم يتحقق بعد"))),
        },
        {
            "key": "reclaim", "ar": "الاستقرار فوق الدعم بعد السحب",
            "std": f"≥ {params.min_reclaim_sessions} إغلاق فوق الدعم +{params.reclaim_buffer_pct:.1f}%",
            "ok": bool(reclaimed and not failed),
            "detail": (f"{reclaim_sessions} إغلاق فوق {reclaim_level:.3f} (آخرها {reclaim_date})"
                       if reclaimed else "لا يوجد إغلاق استعادة بعد"),
        },
    ]

    # حالة كل خطوة: ok / active (الخطوة الجارية) / pending
    first_open = next((i for i, c in enumerate(checks) if not c["ok"]), None)
    for i, c in enumerate(checks):
        c["state"] = ("ok" if c["ok"]
                      else "active" if i == first_open else "pending")
    out.update({
        "stage": stage, "ready": ready, "failed": failed, "checks": checks,
        "support_hold_ok": support_hold_ok, "base_sessions": base_sessions,
        "resistance_kind": resistance_kind, "sequence_ok": sequence_ok,
        "resistance_stall_pct": _r(stall_pct, 2), "resistance_tested": bool(resistance_ok),
        "impulse_pct": _r(impulse_pct, 2), "impulse_high": _r(impulse_high),
        "impulse_low": _r(impulse_low), "impulse_date": impulse_date,
        "resistance_level": _r(resistance_level), "resistance_distance_pct": _r(resistance_distance, 2),
        "resistance_date": resistance_date,
        "pullback_type": pullback_type, "pullback_low": _r(pullback_low), "pullback_date": pullback_date,
        "sweep_low": _r(sweep_low), "sweep_depth_pct": _r(sweep_depth, 2), "sweep_date": sweep_date,
        "reclaim_level": _r(reclaim_level), "reclaim_sessions": reclaim_sessions,
        "reclaim_date": reclaim_date,
        "return_to_support": return_to_support, "reclaimed": reclaimed,
        "base_close": _r(base_close), "support": _r(support),
    })
    return out


def analyse_4h(df4: pd.DataFrame, params: StrategyParams) -> dict[str, Any]:
    if df4 is None or len(df4) < 30:
        return {"available": False}
    last = float(df4["close"].iloc[-1])
    rsi_now = float(df4["rsi"].iloc[-1]) if "rsi" in df4 and pd.notna(df4["rsi"].iloc[-1]) else None
    trend = "neutral"
    if last > float(df4["ema20"].iloc[-1]) > float(df4["ema50"].iloc[-1]):
        trend = "up"
    elif last < float(df4["ema20"].iloc[-1]) < float(df4["ema50"].iloc[-1]):
        trend = "down"
    ch_up = df4["ch_up"].iloc[-1] if "ch_up" in df4 else np.nan
    out = {
        "available": True,
        "last": _r(last),
        "rsi": _r(rsi_now, 2),
        "trend": trend,
        "ema20": _r(df4["ema20"].iloc[-1]),
        "ema50": _r(df4["ema50"].iloc[-1]),
        "rvol": _r(df4["rvol"].iloc[-1], 2) if "rvol" in df4 else None,
        "macd_hist": _r(df4["macd_hist"].iloc[-1], 5) if "macd_hist" in df4 else None,
        "channel_broken": bool(pd.notna(ch_up) and last > float(ch_up)),
        "higher_lows": ind.count_higher_lows(df4, df4.index[-40]),
        "bullish_divergence": bool(ind.bullish_divergence(df4, df4["rsi"], lookback=60)),
    }
    out["aligned"] = bool(
        trend == "up" and (rsi_now is not None and rsi_now > params.rsi_oversold)
    )
    # نعكس ترتيب الشموع لعرضها زمنياً تصاعدياً في الواجهة
    return out


# --------------------------------------------------------------------------- #
#  التقييم
# --------------------------------------------------------------------------- #
def score_setup(a: Analysis, params: StrategyParams) -> list[ScorePart]:
    w = params.weights
    parts: list[ScorePart] = []

    # 1) حدث التقسيم: كلما كان أحدث ونسبته أوضح كانت الدرجة أعلى
    rs_score = 0.0
    if a.rs_found:
        age = a.rs_age_days or 0
        rs_score = _scaled(age, [(0, 100), (10, 95), (25, 85), (45, 70), (60, 55)])
        ratio_bonus = _scaled(a.rs_ratio, [(2, -8), (5, 0), (10, 6), (20, 8), (40, 4), (60, -4)])
        rs_score = float(np.clip(rs_score + ratio_bonus, 0, 100))
    parts.append(ScorePart(
        "rs_setup", "حدث التقسيم العكسي", rs_score, w["rs_setup"],
        f"1-for-{a.rs_ratio:g} بتاريخ {a.rs_date} (عمر {a.rs_age_days} يوم)" if a.rs_found else "غير متحقق"))

    # 2) صعود يوم التقسيم
    spike = _scaled(a.spike_pct, [(0, 0), (5, 45), (10, 70), (15, 92), (25, 100), (60, 80)])
    parts.append(ScorePart("spike", "صعود يوم التقسيم", spike, w["spike"],
                           f"{a.spike_pct:.1f}% من الافتتاح {a.rs_open} إلى {a.spike_high}"
                           if a.spike_pct is not None else "غير متحقق"))

    # 3) عمق الموجة الهابطة
    drop = _scaled(abs(a.decline_pct) if a.decline_pct is not None else None,
                   [(0, 0), (22, 30), (30, 62), (40, 84), (50, 96), (70, 100)])
    parts.append(ScorePart("decline", "قوة الموجة الهابطة", drop, w["decline"],
                           f"هبوط {abs(a.decline_pct):.1f}% من القمة {a.spike_high} إلى القاع {a.bottom_price}"
                           if a.decline_pct is not None else "غير متحقق"))

    # 4) التشبع البيعي وتحسّنه
    over = _scaled(a.rsi_trough, [(40, 0), (35, 25), (30, 60), (25, 85), (20, 100), (10, 100)])
    if a.rsi_recovered:
        over = min(100.0, over + 8)
    if (a.rsi_sessions_below_30 or 0) >= 3:
        over = min(100.0, over + 5)
    parts.append(ScorePart("oversold", "التشبع البيعي", float(np.clip(over, 0, 100)), w["oversold"],
                           f"أدنى RSI {a.rsi_trough} ({a.rsi_trough_date}) — الحالي {a.rsi_now}"
                           + (" وقد بدأ التحسّن" if a.rsi_recovered else "")))

    # 5) القاع والثبات
    s = a.base_sessions or 0
    base_score = _scaled(s, [(0, 0), (1, 20), (2, 45), (3, 78), (5, 100), (6, 100),
                             (10, 88), (15, 70), (20, 55), (30, 35)])
    detail_bits = [f"ثبات ({sessions_ar(s)})", f"نطاق القاعدة {a.base_range_pct:.1f}%" if a.base_range_pct is not None else ""]
    if a.volume_dryup is not None:
        detail_bits.append(f"جفاف حجم {a.volume_dryup*100:.0f}% من المتوسط")
        if a.volume_dryup < params.max_volume_dryup_ratio:
            base_score = min(100.0, base_score + 6)
    if a.base_broken:
        base_score *= 0.25
        detail_bits.append("⚠ كسر القاع")
    parts.append(ScorePart("base", "القاع والثبات", float(np.clip(base_score, 0, 100)), w["base"],
                           " — ".join([b for b in detail_bits if b])))

    # 6) تأكيد الانعكاس
    rev = 0.0
    rev_detail: list[str] = []
    if a.above_ema9_sessions >= params.min_sessions_above_ema9:
        rev += 25
        rev_detail.append(f"إغلاق {sessions_ar(a.above_ema9_sessions)} فوق EMA9")
    elif a.above_ema9_sessions == 1:
        rev += 12
        rev_detail.append("جلسة واحدة فوق EMA9")
    if a.macd_turning:
        rev += 20
        rev_detail.append("MACD يتحسّن")
    if a.channel_broken:
        rev += 25
        rev_detail.append("اختراق القناة الهابطة")
    if (a.higher_lows or 0) >= 1:
        rev += min(15, 8 * a.higher_lows)
        rev_detail.append(f"{a.higher_lows} قاع صاعد")
    if a.rsi_recovered:
        rev += 15
        rev_detail.append("RSI خارج التشبع")
    if (a.rvol or 0) >= params.rvol_threshold:
        rev += 10
        rev_detail.append(f"حجم نسبي {a.rvol}x")
    if a.h4.get("aligned"):
        rev += 10
        rev_detail.append("توافق فريم 4 ساعات")

    # دورة الحياة: انطلاقة → مقاومة → عودة للدعم/سحب سيولة → استعادة
    if a.impulse_pct is not None and a.impulse_pct >= params.min_ready_impulse_pct:
        closeness = 1 - min(1.0, abs(a.impulse_pct - params.ideal_impulse_pct) / params.ideal_impulse_pct)
        rev += 8 + 6 * closeness
        rev_detail.append(f"انطلاقة +{a.impulse_pct:.0f}% بعد الثبات")
    if a.resistance_level:
        rev += 6
        rev_detail.append(f"اختبار مقاومة قريبة عند {a.resistance_level}")
    if a.pullback_type == "liquidity_sweep":
        rev += 12
        rev_detail.append(f"سحب سيولة تحت الدعم {a.sweep_low} ثم استعادة")
    elif a.pullback_type == "support_retest":
        rev += 8
        rev_detail.append("إعادة اختبار الدعم الرئيسي")
    if a.lifecycle_ready:
        rev += 18
        rev_detail.append("تسلسل «جاهز فنياً» مكتمل (انطلاقة → مقاومة → عودة للدعم/سحب سيولة → استعادة)")
    elif a.lifecycle_failed:
        rev -= 20
        rev_detail.append("فشل التسلسل — كسر الدعم دون استعادة")
    parts.append(ScorePart("reversal", "تأكيد الانعكاس", float(np.clip(rev, 0, 100)), w["reversal"],
                           "، ".join(rev_detail) or "لا يوجد تأكيد فني بعد"))

    # 7) عوامل الانفجار (فلوت صغير + بيع مكشوف + ضغط تقلب)
    squeeze = 0.0
    sq_bits: list[str] = []
    if a.short_pct_float is not None:
        squeeze += _scaled(a.short_pct_float, [(3, 2), (8, 8), (15, 16), (25, 22), (40, 25)])
        sq_bits.append(f"بيع مكشوف {a.short_pct_float:.1f}% من الفلوت")
    if a.float_shares:
        squeeze += _scaled(a.float_shares / 1e6, [(0.5, 18), (2, 14), (5, 8), (15, 3), (50, 0)])
        sq_bits.append(f"فلوت {a.float_shares/1e6:.2f}M سهم")
    if a.days_to_cover:
        squeeze += _scaled(a.days_to_cover, [(0.5, 0), (1.5, 3), (3, 6), (6, 8)])
        sq_bits.append(f"أيام تغطية {a.days_to_cover:.1f}")
    if a.squeeze:
        squeeze += 6
        sq_bits.append("ضغط تقلب (Squeeze)")
    if a.short_change_pct:
        sq_bits.append(f"تغيّر المراكز {a.short_change_pct:+.0f}%")
    parts.append(ScorePart("squeeze", "عوامل الضغط والانفجار", float(np.clip(squeeze, 0, 100)),
                           w["squeeze"], "، ".join(sq_bits) or "لا بيانات كافية"))

    # 8) المخاطر (تخفيف وأخبار سلبية وسيولة)
    risk = 100.0
    rk_bits: list[str] = []
    recent_neg = [c for c in a.negative_news
                  if _days_since(c.get("date")) is not None and _days_since(c["date"]) <= 30]
    for c in recent_neg:
        if c.get("type") == "offering":
            risk -= 34
            rk_bits.append("طرح أسهم خلال 30 يوماً")
        elif c.get("impact") == "high":
            risk -= 15
            rk_bits.append("خبر سلبي عالي الأثر")
        else:
            risk -= 7
    hi_dil = [d for d in a.dilution if d.get("severity") == "high"
              and (_days_since(d.get("date")) or 999) <= 45]
    if hi_dil:
        risk -= 18
        rk_bits.append(f"{len(hi_dil)} إفصاح تخفيف عالي الخطورة")
    if (a.dollar_volume or 0) < params.min_avg_dollar_volume:
        risk -= 20
        rk_bits.append("سيولة متوسطة منخفضة")
    if a.base_broken:
        risk -= 20
        rk_bits.append("كسر القاع المرجعي")
    if a.lifecycle_failed:
        risk -= 25
        rk_bits.append("فشل تسلسل الانعكاس (كسر الدعم بعد الانطلاقة)")
    if a.pullback_type == "liquidity_sweep":
        rk_bits.append("سحب سيولة تحت الدعم حدث ثم تمت استعادته")
    parts.append(ScorePart("risk", "جودة المخاطر", float(np.clip(risk, 0, 100)), w["risk"],
                           "، ".join(rk_bits) or "لا مخاطر بارزة خلال 30 يوماً"))
    return parts


def _days_since(date_str) -> int | None:
    try:
        d = pd.Timestamp(date_str).normalize()
        if d > pd.Timestamp.now():
            return 0
        return int((pd.Timestamp.now().normalize() - d).days)
    except Exception:  # noqa: BLE001
        return None


def grade_for(score: float) -> str:
    if score >= 85:
        return "A+"
    if score >= 75:
        return "A"
    if score >= 65:
        return "B"
    if score >= 52:
        return "C"
    return "D"


# --------------------------------------------------------------------------- #
#  المرحلة
# --------------------------------------------------------------------------- #
def classify_phase(a: Analysis, params: StrategyParams) -> int:
    """تحديد مرحلة السهم وفق تسلسل الاستراتيجية."""
    if not a.rs_found:
        return 0
    peak = a.spike_high or 0
    last = a.last_close or 0
    off_peak = _pct(last, peak) if peak else None

    # تمدد: ارتفع كثيراً عن القاع
    if a.bottom_price and last > a.bottom_price * 1.45:
        return 7
    # 1) تقسيم مرصود
    if a.spike_high is None:
        return 1
    # 2) ما زال في الهبوط أو لم يتحقق شرط الهبوط بعد
    if (a.decline_pct is None) or (a.spike_pct is not None and a.spike_pct < params.min_spike_pct):
        return 2 if (off_peak or 0) > -params.min_drop_pct else 1
    if a.base_broken and (a.base_sessions or 0) < params.min_base_sessions:
        return 2
    if (a.base_sessions or 0) < params.min_base_sessions:
        if a.bottom_price and last <= a.bottom_price * (1 + params.base_tolerance_pct / 100.0):
            return 3 if (a.base_sessions or 0) >= 1 else 2
        return 2
    # 3) القاعدة مؤكدة — نطبّق الآن تسلسل دورة الحياة (شرط «جاهز فنياً»)
    confirmations = []
    if a.rsi_recovered:
        confirmations.append("rsi")
    if (a.above_ema9_sessions or 0) >= params.min_sessions_above_ema9:
        confirmations.append("ema9")
    if a.macd_turning:
        confirmations.append("macd")
    if a.channel_broken or (a.rvol or 0) >= params.rvol_threshold:
        confirmations.append("break")
    if (a.higher_lows or 0) >= 1:
        confirmations.append("hl")

    if a.lifecycle_failed:
        return 2                                    # فشل التسلسل = عودة لمرحلة الهبوط
    if params.require_lifecycle_sequence:
        if not a.support_hold_ok and (a.base_sessions or 0) < params.min_base_sessions:
            return 4                                 # القاعدة نفسها لم تكتمل (أقل من 3 جلسات)
        if a.lifecycle_ready:
            # اكتمال التسلسل = شرط «جاهز فنياً»؛ ونطلب تأكيداً فنياً واحداً إضافياً على الأقل
            return 6 if len(confirmations) >= 1 else 5
        if a.impulse_pct is not None or confirmations:
            return 5                                # التسلسل/التحسّن قيد التكوين
        return 4
    # سلوك بديل (لو أُعطِل إلزام التسلسل)
    if len(confirmations) >= 3:
        return 6
    if confirmations:
        return 5
    return 4


# --------------------------------------------------------------------------- #
#  المستويات الفنية وخطة التداول
# --------------------------------------------------------------------------- #
def build_levels(a: Analysis, df: pd.DataFrame, params: StrategyParams) -> dict[str, Any]:
    """مستويات الارتكاز: نقطة الاختراق، الوقف التداولي، وقف إلغاء السيناريو، والأهداف."""
    last = a.last_close or float(df["close"].iloc[-1])
    bottom = a.bottom_price or float(df["low"].tail(20).min())
    peak = a.spike_high or float(df["high"].tail(60).max())
    base_high = a.base_high or float(df["high"].tail(5).max())
    recent_high = float(df["high"].tail(5).max())
    ema9 = float(df["ema9"].iloc[-1]) if "ema9" in df else last
    ema20 = float(df["ema20"].iloc[-1]) if "ema20" in df else last
    atr_pct = a.atr_pct or 8.0

    # نقطة الاختراق: أعلى القاعدة/آخر 5 جلسات، مع سقف 25% فوق السعر الحالي
    structural = max(base_high, recent_high)
    if a.lifecycle_ready and a.reclaim_level:
        # بعد اكتمال التسلسل: الاختراق يكون فوق قمة الاستعادة/المقاومة القريبة
        structural = max(structural, min(a.resistance_level or structural, last * 1.35))
    trigger_raw = max(structural, min(ema9, last * 1.08)) * 1.012
    trigger = float(min(max(trigger_raw, last * 1.015), last * 1.25))

    # وقف إلغاء السيناريو (بنيوي): تحت قاع السحب إن وُجد، وإلا تحت القاع المرجعي
    invalidation_base = bottom
    if a.pullback_type == "liquidity_sweep" and a.sweep_low:
        invalidation_base = min(bottom, float(a.sweep_low))
    stop_structural = invalidation_base * (1 - max(0.02, min(0.06, atr_pct / 300.0)))
    # الوقف التداولي: مسافة 1.4×ATR بحدود 8%–20% من نقطة الدخول
    stop_width = max(0.08, min(0.20, 1.4 * atr_pct / 100.0))
    stop = float(max(stop_structural, trigger * (1 - stop_width)))
    if stop >= trigger * 0.995:
        stop = trigger * 0.92

    span = max(peak - bottom, 1e-9)
    t1, t2, t3 = bottom + 0.382 * span, bottom + 0.618 * span, peak
    risk = max(trigger - stop, 1e-6)
    zone_lo = max(bottom * 1.005, min(stop * 1.01, last * 0.98))
    return {
        "trigger": _r(trigger),
        "entry_zone": [_r(zone_lo), _r(trigger)],
        "stop": _r(stop),
        "stop_pct": _r(_pct(stop, trigger), 2),
        "stop_structural": _r(stop_structural),
        "stop_structural_pct": _r(_pct(stop_structural, trigger), 2),
        "targets": {"t1_382": _r(t1), "t2_618": _r(t2), "t3_full": _r(t3)},
        "rr_t1": _r((t1 - trigger) / risk, 2),
        "rr_t2": _r((t2 - trigger) / risk, 2),
        "risk_per_share": _r(risk),
        "reward_t2_per_share": _r(t2 - trigger),
        "atr_pct": _r(atr_pct, 2),
        "bottom": _r(bottom), "peak": _r(peak), "last": _r(last),
        "base_high": _r(base_high), "ema9": _r(ema9), "ema20": _r(ema20),
        "support": _r(a.support_level or bottom),
        "impulse_high": _r(a.impulse_high), "resistance": _r(a.resistance_level),
        "sweep_low": _r(a.sweep_low), "reclaim_level": _r(a.reclaim_level),
        "position_note_ar": (
            f"مخاطرة السهم عالية (ATR {atr_pct:.1f}%) — استخدم حجم مركز صغير؛ "
            f"مثال: مخاطرة 0.5-1% من رأس المال لكل صفقة، والوقف عند {_r(stop)}$ "
            f"({_r(_pct(stop, trigger), 2)}% من نقطة الدخول)."
        ),
    }


# --------------------------------------------------------------------------- #
#  السرد التحليلي
# --------------------------------------------------------------------------- #
def build_narrative(a: Analysis, params: StrategyParams) -> tuple[list[str], str]:
    why: list[str] = []
    if a.rs_found:
        why.append(
            f"تقسيم عكسي {a.rs_ratio_str} نافذ في {a.rs_date} (عمر {a.rs_age_days} يوماً) — "
            f"داخل نافذة الشهرين المطلوبة بالاستراتيجية."
        )
    if a.spike_pct is not None:
        why.append(
            f"افتتح يوم التقسيم على {a.rs_open}$ وصعد إلى {a.spike_high}$ "
            f"({a.spike_pct:+.1f}%) ثم فشل في الثبات فوق الصعود."
        )
    if a.decline_pct is not None:
        why.append(
            f"موجة هبوط {abs(a.decline_pct):.1f}% من القمة {a.spike_high}$ إلى القاع المرجعي "
            f"{a.bottom_price}$ بتاريخ {a.bottom_date}"
            + (" — هبوط يتجاوز شرط 50% (سيناريو مثالي)." if abs(a.decline_pct) >= 50
               else " — يحقق شرط 30% على الأقل." if abs(a.decline_pct) >= 30 else " — أقل من شرط 30%.")
        )
    if a.rsi_trough is not None:
        why.append(
            f"RSI وصل إلى {a.rsi_trough} ({'تشبع بيعي متطرف' if a.rsi_trough < 20 else 'تشبع بيعي قوي' if a.rsi_trough < 25 else 'تشبع بيعي'} "
            f"و {a.rsi_sessions_below_30} جلسة تحت 30) والحالي {a.rsi_now}"
            + (" مع بدء التحسّن." if a.rsi_recovered else " ولم يتأكد التحسّن بعد.")
        )
    why.append(
        f"ثبات {sessions_ar(a.base_sessions)} على القاع {a.bottom_price}$ "
        + (f"داخل نطاق {a.base_range_pct:.1f}%" if a.base_range_pct is not None else "")
        + (f" مع جفاف حجم ({a.volume_dryup*100:.0f}% من المتوسط)" if a.volume_dryup else "")
        + ("، والقاع مكسور ⚠" if a.base_broken else "، والقاع صامد.")
    )
    # دورة الحياة بعد الثبات (شروط «جاهز فنياً»)
    if a.impulse_pct is not None:
        quality = ("بالقدر المطلوب (≈20%)" if abs(a.impulse_pct - params.ideal_impulse_pct) <= 5
                   else "أعلى من المستهدف قليلاً" if a.impulse_pct > params.ideal_impulse_pct
                   else "أقل من المستهدف (20%) قليلاً")
        why.append(
            f"الانطلاقة بعد الثبات: +{a.impulse_pct:.1f}% من إغلاق القاعدة ({a.base_close}$) إلى {a.impulse_high}$ "
            f"({a.impulse_date}) — {quality}."
        )
    if a.resistance_level:
        why.append(
            f"أقرب مقاومة عند {a.resistance_level}$ (تشكّلت {a.resistance_date})"
            + (f" — على {a.resistance_distance_pct:.1f}% من الدعم الرئيسي" if a.resistance_distance_pct is not None else "")
            + (f"، واختُبرها السعر في {a.impulse_date} عند {a.impulse_high}$ " if a.impulse_date else "")
            + (f"وتوقف عندها بفارق {a.resistance_stall_pct:.1f}% ثم تراجع." if a.resistance_stall_pct is not None else "ثم تراجع.")
        )
    if a.pullback_type == "liquidity_sweep":
        why.append(
            f"سحب سيولة (Liquidity Sweep) تحت الدعم: هبطت الفتيل إلى {a.sweep_low}$ "
            f"({a.sweep_depth_pct:.1f}% تحت الدعم {a.bottom_price}$) في {a.sweep_date} "
            f"ثم أُغلق فوق الدعم في {sessions_ar(a.reclaim_sessions)} استعادة (آخرها {a.reclaim_date})."
        )
    elif a.pullback_type == "support_retest":
        why.append(
            f"عاد السهم لاختبار الدعم الرئيسي {a.bottom_price}$ (أدنى نقطة {a.pullback_low}$ "
            f"بتاريخ {a.pullback_date}) وارتد دون كسر — إعادة اختبار ناجحة."
        )
    elif a.pullback_type == "breakdown":
        why.append(
            f"⚠ كسر الدعم الرئيسي إلى {a.pullback_low}$ دون استعادة — فشل التسلسل وإلغاء السيناريو."
        )
    if a.lifecycle_stage:
        why.append(f"حالة التسلسل: {a.lifecycle_stage}.")
    if a.confirmations:
        why.append("تأكيدات فنية: " + "، ".join(a.confirmations) + ".")
    if a.h4.get("available"):
        why.append(
            f"فريم 4 ساعات: الاتجاه { {'up': 'صاعد', 'down': 'هابط', 'neutral': 'عرضي'}.get(a.h4.get('trend'), 'عرضي') }"
            f"، RSI {a.h4.get('rsi')}"
            + ("، والقناة الهابطة مكسورة على الفريم." if a.h4.get("channel_broken") else "")
        )
    if a.float_shares:
        why.append(f"عدد الأسهم {_fmt_num(a.shares_out)} — الفلوت {_fmt_num(a.float_shares)} سهم.")
    if a.short_pct_float is not None:
        why.append(f"البيع المكشوف {a.short_pct_float:.1f}% من الفلوت (أيام التغطية {a.days_to_cover or '—'}).")
    if a.patterns_daily:
        top = a.patterns_daily[0]
        why.append(f"أبرز نموذج فني: {top['name_ar']} — {top['description_ar']}")
    if a.negative_news:
        why.append("أخبار سلبية: " + " | ".join(c["title"][:80] for c in a.negative_news[:2]))
    if a.positive_news:
        why.append("محفزات إيجابية: " + " | ".join(c["title"][:80] for c in a.positive_news[:2]))

    # خطة العمل — GAM والمستبعدات لا تحصل أبداً على صيغة دخول أو توصية.
    lv = a.levels or {}
    if a.gam_candidate:
        action = (
            "مرشح GAM للمراجعة في المسار الثانوي فقط؛ لم يجتز الأهلية الصارمة بسبب "
            + " و".join(a.gam_reasons)
            + ". لا يُعرض كفرصة مؤهلة أو في قائمة المتابعة، وليس توصية أو إشارة دخول."
        )
    elif not a.eligible:
        action = "غير مؤهل حالياً — يبقى في الجدول العام كمستبعد، ولا توجد إشارة دخول أو توصية."
    elif a.phase == 6:
        action = (
            f"جاهز فنياً (التسلسل مكتمل: انطلاقة → مقاومة → عودة للدعم/سحب سيولة → استعادة): "
            f"الدخول بعد اختراق {lv.get('trigger')} بحجم تداول ≥ {params.rvol_threshold}x، "
            f"والوقف {lv.get('stop')} ({lv.get('stop_pct')}%)"
            + (f" (تحت قاع السحب {a.sweep_low})" if a.pullback_type == "liquidity_sweep" and a.sweep_low else "")
            + f"، والأهداف {lv.get('targets',{}).get('t1_382')} / {lv.get('targets',{}).get('t2_618')} / "
            f"{lv.get('targets',{}).get('t3_full')} — نسبة مخاطرة/عائد {lv.get('rr_t2')}."
        )
    elif a.phase == 5:
        action = (f"المرحلة الإيجابية المبكرة ({a.lifecycle_stage or 'التسلسل قيد التكوّن'}): "
                  f"تجهيز أمر شرطيان فوق {lv.get('trigger')} — لا دخول قبل اكتمال التسلسل "
                  f"(انطلاقة → مقاومة → عودة للدعم → استعادة) بتأكيد الحجم.")
    elif a.phase == 4:
        action = (
            f"قائمة المتابعة: الثبات على القاع {a.bottom_price}$ مؤكد ({sessions_ar(a.base_sessions)}) — "
            f"المتابعة اليومية باتجاه اختراق {lv.get('trigger')}. أي كسر لـ "
            f"{lv.get('stop')} يُلغي السيناريو."
        )
    elif a.phase == 3:
        action = f"قاع يتشكّل: راقب ثبات 3 جلسات فوق {a.bottom_price}$ قبل أي تفكير بالدخول."
    elif a.phase == 2:
        action = "موجة هبوط واستنزاف بيعي: لا دخول قبل تكوين قاع وثبات (القاعدة الذهبية: لا نشتري سكيناً ساقطة)."
    elif a.phase == 7:
        action = "ممتد بعد الاختراق: انتظر إعادة اختبار القاعدة أو استخدام حجم مركز أصغر بكثير."
    else:
        action = "غير مؤهل حالياً — يبقى في قائمة المراقبة العامة."
    return why, action


def sessions_ar(n) -> str:
    """صياغة عربية صحيحة لعدد الجلسات."""
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return "جلسة"
    if n == 1:
        return "جلسة واحدة"
    if n == 2:
        return "جلستان"
    if 3 <= n <= 10:
        return f"{n} جلسات"
    return f"{n} جلسة"


def _fmt_num(x) -> str:
    if x is None:
        return "—"
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "—"
    if x >= 1e9:
        return f"{x/1e9:.2f}B"
    if x >= 1e6:
        return f"{x/1e6:.2f}M"
    if x >= 1e3:
        return f"{x/1e3:.1f}K"
    return f"{x:.0f}"


# --------------------------------------------------------------------------- #
#  التحليل الكامل لرمز واحد
# --------------------------------------------------------------------------- #
def analyze_symbol(symbol: str, hub: DataHub, params: StrategyParams = STRATEGY) -> Analysis:
    symbol = symbol.upper()
    a = Analysis(symbol=symbol)
    a.data_source = hub.effective_mode

    raw_daily = hub.daily(symbol, 320)
    if raw_daily is None or len(raw_daily) < 40:
        reason = hub.rejections.get(symbol, "لا توجد بيانات سعرية كافية")
        add_rejection(a, "missing_price_data", reason)
        a.synthetic = (hub.effective_mode == "demo")
        return a
    df = ind.add_indicators(raw_daily, params)

    prof: TickerProfile | None = hub.profile(symbol)
    if prof is None:
        reason = hub.rejections.get(symbol, "لا توجد بيانات حقيقية لهذا الرمز")
        add_rejection(a, "missing_profile_data", reason)
        a.synthetic = False  # وُجدت أسعار فقط بدون ملف تعريفي — ليس تجريبي
        a.price_source = "yahoo-finance"
        return a
    a.name = prof.name or symbol
    a.sector = prof.sector or ""
    a.industry = prof.industry or ""
    a.exchange = prof.exchange or ""
    a.shares_out = prof.shares_out
    a.float_shares = prof.float_shares
    a.market_cap = prof.market_cap
    a.avg_volume = prof.avg_volume
    a.insider_pct = prof.insider_pct
    a.institutional_pct = prof.institutional_pct

    # تسجيل مصادر البيانات
    is_live = hub.mode == "live"
    a.synthetic = not is_live
    a.price_source = "yahoo-finance" if is_live else "demo-generator"
    a.fundamentals_source = prof.source or ("yahoo-finance" if is_live else "demo-generator")

    a.last_close = _r(df["close"].iloc[-1]) or 0.0
    a.change_pct = _r(_pct(df["close"].iloc[-1], df["close"].iloc[-2]), 2) or 0.0
    a.as_of = _date_str(df.index[-1]) or ""
    a.dollar_volume = _r(df["dollar_vol"].iloc[-1], 0) or 0.0

    # ---------- مؤشرات عامة (تُحسب دائماً) ---------- #
    quick = quick_momentum(df, params)
    for k, v in quick.items():
        if not hasattr(a, k) or getattr(a, k) in (None, 0):
            setattr(a, k, v)
    a.atr_pct = quick.get("atr_pct") or _r(df["atr_pct"].iloc[-1], 2)
    a.higher_lows = int(quick.get("higher_lows") or 0)

    # ---------- التقسيم ---------- #
    events = hub.splits(symbol)
    ev, rs_reasons = pick_reverse_split(events, params, pd.Timestamp(df.index[-1]))
    if ev is None:
        # لا حدث حديث صالح: أسباب تجاهل الأحداث القديمة/غير المطابقة تصبح أسباب رفض.
        for reason in rs_reasons:
            add_rejection(a, "reverse_split", reason)
    else:
        # حدث حديث صالح مختار: تجاهل الأحداث التاريخية ملاحظة فقط — لا يُبطل الأهلية.
        a.notes.extend(f"حدث تاريخي متجاهَل: {reason}" for reason in rs_reasons)
    size_ok = (params.min_price <= a.last_close <= params.max_price)
    if not size_ok:
        add_rejection(
            a, "price",
            f"سعر الإقفال {a.last_close}$ خارج نطاق الاستراتيجية "
            f"({params.min_price}$ - {params.max_price}$)",
        )
    if (a.dollar_volume or 0) < params.min_avg_dollar_volume:
        add_rejection(
            a, "liquidity",
            f"سيولة متوسطة منخفضة ({_fmt_num(a.dollar_volume)}$ يومياً < "
            f"{_fmt_num(params.min_avg_dollar_volume)}$)",
        )
    if params.max_float_shares and (a.float_shares or 0) > params.max_float_shares:
        add_rejection(a, "float", f"الفلوت {_fmt_num(a.float_shares)} أكبر من الحد المسموح")

    # مصدر التقسيم: من الحدث المختار (yahoo/sec/demo)
    if ev is not None:
        a.rs_found = True
        a.rs_date = _date_str(ev.date)
        a.rs_age_days = ev.age_days
        a.rs_ratio_str = ev.ratio_str or f"1-for-{ev.factor:g}"
        a.rs_ratio = float(ev.factor)
        a.rs_source = ev.source
        a.split_source = ev.source or a.data_source
        a.rs_url = ev.url
        struct = analyse_structure(df, ev, params)
        mom = analyse_momentum(df, struct, params)
        a.rs_open = struct["rs_open"]
        a.spike_high = struct["spike_high"]
        a.spike_pct = struct["spike_pct"]
        a.spike_date = struct["spike_date"]
        a.peak_date = struct["peak_date"]
        a.decline_pct = struct["decline_pct"]
        a.bottom_price = struct["bottom_price"]
        a.bottom_date = struct["bottom_date"]
        a.base_sessions = struct["base_sessions"]
        a.base_high = struct["base_high"]
        a.base_low = struct["base_low"]
        a.base_range_pct = struct["base_range_pct"]
        a.base_broken = struct["base_broken"]
        a.base_close = struct.get("base_close")
        a.volume_dryup = struct["volume_dryup"]
        for k in ("rsi_now", "rsi_trough", "rsi_trough_date", "rsi_sessions_below_30",
                  "rsi_recovered", "above_ema9_sessions", "macd_turning", "macd_hist",
                  "channel_upper", "channel_slope_pct", "channel_broken", "rvol",
                  "squeeze", "atr_pct", "higher_lows"):
            setattr(a, k, mom.get(k))
        a.higher_lows = int(mom.get("higher_lows") or 0)

        # ---------- دورة الحياة بعد الثبات (شروط «جاهز فنياً») ---------- #
        seq = analyse_lifecycle(df, struct, params)
        a.spark = [_r(float(x), 3) for x in df["close"].tail(40).tolist()]
        a.lifecycle_stage = seq["stage"]
        a.lifecycle_ready = bool(seq["ready"])
        a.lifecycle_failed = bool(seq["failed"])
        a.lifecycle_checks = seq["checks"]
        a.impulse_pct = seq["impulse_pct"]
        a.impulse_high = seq["impulse_high"]
        a.impulse_date = seq["impulse_date"]
        a.resistance_level = seq["resistance_level"]
        a.resistance_distance_pct = seq["resistance_distance_pct"]
        a.resistance_date = seq["resistance_date"]
        a.pullback_type = seq["pullback_type"]
        a.pullback_low = seq["pullback_low"]
        a.pullback_date = seq["pullback_date"]
        a.sweep_low = seq["sweep_low"]
        a.sweep_depth_pct = seq["sweep_depth_pct"]
        a.sweep_date = seq["sweep_date"]
        a.reclaim_level = seq["reclaim_level"]
        a.reclaim_sessions = int(seq["reclaim_sessions"] or 0)
        a.reclaim_date = seq["reclaim_date"]
        a.support_level = seq.get("support")
        a.support_hold_ok = bool(seq.get("support_hold_ok"))
        a.lifecycle_sequence_ok = bool(seq.get("sequence_ok"))
        a.resistance_kind = seq.get("resistance_kind")
        a.resistance_tested = bool(seq.get("resistance_tested"))
        a.resistance_stall_pct = seq.get("resistance_stall_pct")

        if a.lifecycle_failed:
            add_rejection(
                a, "lifecycle_failure",
                "كسر الدعم الرئيسي بعد الانطلاقة دون استعادة (سحب سيولة فاشل) — "
                "إلغاء السيناريو حتى يتكوّن قاع جديد",
            )
        if a.lifecycle_ready:
            a.notes.append(f"تسلسل الاستراتيجية مكتمل — {a.lifecycle_stage}")
        elif a.impulse_pct is not None and not a.rejections:
            a.notes.append(f"تسلسل «جاهز فنياً» قيد التكوّن — {a.lifecycle_stage}")

        # شروط الاستراتيجية الصارمة
        if a.spike_pct is not None and a.spike_pct < params.min_spike_pct:
            add_rejection(
                a, "spike",
                f"صعود يوم التقسيم {a.spike_pct:.1f}% أقل من الحد الأدنى {params.min_spike_pct}%",
            )
        if a.decline_pct is None:
            add_rejection(a, "missing_decline_data", "بيانات الهبوط بعد التقسيم غير كافية للتحقق")
        elif abs(a.decline_pct) < params.min_drop_pct:
            add_rejection(
                a, "decline",
                f"الهبوط {abs(a.decline_pct):.1f}% أقل من الحد المطلوب {params.min_drop_pct}%",
            )
        if a.rsi_trough is None:
            add_rejection(a, "missing_rsi_data", "بيانات RSI غير كافية للتحقق من التشبع البيعي")
        elif a.rsi_trough >= params.rsi_oversold:
            add_rejection(
                a, "rsi",
                f"RSI لم ينزل تحت {params.rsi_oversold:g} (أدنى قيمة {a.rsi_trough}) — لا يوجد تشبع بيعي",
            )
        if (a.base_sessions or 0) < params.min_base_sessions:
            a.notes.append(
                f"الثبات على القاع {sessions_ar(a.base_sessions)} — لم يكتمل شرط "
                f"{params.min_base_sessions} جلسات بعد (مرحلة تكوّن القاعدة)")
        if a.base_broken:
            add_rejection(a, "base_broken", "كسر القاع المرجعي بأكثر من الحد المسموح (السيناريو مهدد)")

    # ---------- البيع المكشوف ---------- #
    sd: ShortData | None = hub.short_data(symbol, a.float_shares)
    if sd is not None:
        a.short_pct_float = _r(sd.short_pct_float, 2)
        a.short_shares = _r(sd.short_shares, 0)
        a.days_to_cover = _r(sd.days_to_cover, 2)
        a.short_change_pct = _r(sd.change_pct, 1)
        a.short_volume_ratio = _r(sd.short_volume_ratio, 3)
        a.short_source = sd.source
        if a.short_pct_float is None and a.short_volume_ratio is not None and a.float_shares:
            # تقدير: نسبة حجم البيع المكشوف اليومي كمؤشر ضغط (لا تُخلط مع نسبة الفلوت)
            pass

    # ---------- الأخبار والمخاطر ---------- #
    cats: list[Catalyst] = hub.catalysts(symbol)
    a.catalysts = [c.to_dict() for c in cats]
    dil: list[DilutionSignal] = hub.dilution(symbol)
    a.dilution = [d.to_dict() for d in dil]
    # مصدر الأخبار
    if cats:
        sources_used = sorted({c.source for c in cats if c.source})
        a.news_source = "+".join(sources_used) if sources_used else a.data_source
    else:
        a.news_source = "" if is_live else "demo-generator"
    a.negative_news = [c.to_dict() for c in cats if c.sentiment == "negative"]
    a.positive_news = [c.to_dict() for c in cats if c.sentiment == "positive"]
    upcoming = [c for c in cats if (_days_since(c.date) is not None and pd.Timestamp(c.date) > pd.Timestamp(a.as_of))]

    hi_recent = [d for d in dil if d.severity == "high" and (_days_since(d.date) or 999) <= params.hard_block_recent_offering_days]
    if hi_recent:
        a.risk_flags.append(
            f"⚠ طرح أسهم/تخفيف عالي الخطورة خلال {params.hard_block_recent_offering_days} أيام: "
            + " | ".join(d.title[:70] for d in hi_recent[:2])
        )
    if a.base_broken:
        a.risk_flags.append("⚠ كسر القاع المرجعي — احتمال استمرار الهبوط")
    if a.synthetic:
        a.risk_flags.append("ℹ البيانات المعروضة تجريبية (demo) — فعّل الوضع الحي من الإعدادات")
    # ضبط قيم افتراضية لمصادر البيانات في حال لم تُحدّث
    if not a.split_source:
        a.split_source = a.rs_source or ("" if is_live else "demo-generator")
    if not a.price_source:
        a.price_source = ("yahoo-finance" if is_live else "demo-generator")
    if not a.fundamentals_source:
        a.fundamentals_source = ("" if is_live else "demo-generator")
    if not a.news_source:
        a.news_source = ("" if is_live else "demo-generator")
    if upcoming:
        a.risk_flags.append("محفزات قادمة: " + " | ".join(
            f"{c.date} {c.title[:45]}" for c in sorted(upcoming, key=lambda x: x.date)[:3]))

    # ---------- النماذج الفنية ---------- #
    ctx = {"ref_low": a.bottom_price, "base_sessions": a.base_sessions,
           "low_date": pd.Timestamp(a.bottom_date) if a.bottom_date else None,
           "rs_date": pd.Timestamp(a.rs_date) if a.rs_date else None}
    a.patterns_daily = [p.to_dict() for p in pat.detect_all(df, "1D", ctx)]
    try:
        df4 = ind.add_indicators(hub.intraday_4h(symbol, 90), params)
        if len(df4) > 30:
            a.h4 = analyse_4h(df4, params)
            a.patterns_4h = [p.to_dict() for p in pat.detect_all(df4, "4H", {
                "ref_low": float(df4["low"].tail(20).min()),
                "base_sessions": min(6, max(3, a.base_sessions or 3)),
                "low_date": df4.index[max(0, len(df4) - 20)],
            })]
    except Exception as exc:  # noqa: BLE001
        a.h4 = {"available": False, "error": str(exc)[:120]}

    # ---------- المرحلة والتأكيدات ---------- #
    conf: list[str] = []
    if a.above_ema9_sessions and a.above_ema9_sessions >= params.min_sessions_above_ema9:
        conf.append(f"إغلاق {sessions_ar(a.above_ema9_sessions)} فوق EMA9")
    if a.macd_turning:
        conf.append("MACD يتجه للتحسّن")
    if a.channel_broken:
        conf.append("اختراق سقف القناة الهابطة")
    if a.rsi_recovered:
        conf.append("RSI خرج من التشبع البيعي وتحسّن")
    if a.higher_lows:
        conf.append(f"{a.higher_lows} قاع صاعد في البنية")
    if (a.rvol or 0) >= params.rvol_threshold:
        conf.append(f"حجم نسبي {a.rvol}x يدعم الحركة")
    if a.impulse_pct is not None and a.impulse_pct >= params.min_ready_impulse_pct and not a.lifecycle_ready:
        conf.append(f"انطلاقة +{a.impulse_pct:.0f}% بعد الثبات (التسلسل قيد التكوين)")
    if a.lifecycle_ready:
        conf.append(
            f"دورة الحياة مكتملة: ثبات {sessions_ar(a.base_sessions)} على الدعم {a.support_level or a.bottom_price}$ "
            f"← انطلاقة +{a.impulse_pct:.0f}% ← مقاومة {a.resistance_level}$ ← "
            + (f"سحب سيولة إلى {a.sweep_low}$ والعودة فوق الدعم" if a.pullback_type == "liquidity_sweep"
               else "إعادة اختبار الدعم")
            + " ← استعادة"
        )
    elif a.pullback_type == "liquidity_sweep":
        conf.append(f"سحب سيولة تحت الدعم إلى {a.sweep_low}$ ثم عودة الإغلاق فوقه")
    elif a.pullback_type == "support_retest":
        conf.append("إعادة اختبار الدعم الرئيسي دون كسر")
    if a.h4.get("aligned"):
        conf.append("توافق فريم 4 ساعات (اتجاه صاعد)")
    if a.h4.get("bullish_divergence"):
        conf.append("ديفرجنس إيجابي على فريم 4 ساعات")
    a.confirmations = conf

    a.phase = classify_phase(a, params)
    a.eligible = bool(a.rs_found and not a.rejections)
    a.display_phase = a.phase if a.eligible else (a.phase if a.rs_found else 0)
    a.watchlist = bool(
        a.rs_found and not a.rejections and a.phase >= 4
        and (a.base_sessions or 0) >= params.min_base_sessions
    )
    evaluate_gam_candidate(a, params)
    if a.gam_candidate:
        # تأكيد دفاعي: لا تسمح أبداً لمسار العرض الثانوي بالترقية إلى فرصة/متابعة.
        a.eligible = False
        a.watchlist = False
        a.notes.append(GAM_DISCLAIMER_AR)
    elif a.rs_found and not a.eligible:
        a.notes.append("مستبعد من قائمة الفرص — راجع أسباب الاستبعاد")

    parts = score_setup(a, params)
    total = sum(p.points for p in parts) / max(1e-9, sum(p.weight for p in parts)) * 100
    if not a.rs_found:
        total *= 0.55
    if a.base_broken:
        total *= 0.8
    a.score = round(float(np.clip(total, 0, 100)), 1)
    a.grade = grade_for(a.score)
    a.breakdown = [p.to_dict() for p in parts]

    a.levels = build_levels(a, df, params)
    a.why_ar, a.action_ar = build_narrative(a, params)
    a.note_ar = hub.scenario_note(symbol)

    # ---------- السلسلة الزمنية للرسم ----------
    keep = ["open", "high", "low", "close", "volume", "ema9", "ema20", "ema50", "rsi",
            "macd", "macd_signal", "macd_hist", "bb_up", "bb_mid", "bb_low",
            "ch_up", "ch_mid", "ch_low", "rvol", "atr_pct", "stoch_k", "stoch_d", "mfi"]
    cols = [c for c in keep if c in df.columns]
    tail = df[cols].tail(180)
    a.series = {
        "daily": _series_to_json(tail),
        "markers": {
            "rs_date": a.rs_date, "spike_date": a.spike_date, "bottom_date": a.bottom_date,
            "peak_date": a.peak_date,
        },
    }
    return a


def _series_to_json(df: pd.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {"time": [], "open": [], "high": [], "low": [], "close": [], "volume": []}
    for idx, row in df.iterrows():
        out["time"].append(_date_str(idx))
        for c in ("open", "high", "low", "close"):
            out[c].append(_r(row.get(c), 4))
        out["volume"].append(_r(row.get("volume"), 0))
    for c in df.columns:
        if c in {"open", "high", "low", "close", "volume"}:
            continue
        out[c] = [_r(v, 4) for v in df[c].to_numpy()]
    return out


# --------------------------------------------------------------------------- #
#  مسح السوق
# --------------------------------------------------------------------------- #
def scan(symbols: list[str], hub: DataHub, params: StrategyParams = STRATEGY,
         progress=None) -> list[Analysis]:
    results: list[Analysis] = []
    for i, sym in enumerate(symbols):
        try:
            a = analyze_symbol(sym, hub, params)
        except Exception as exc:  # noqa: BLE001
            a = Analysis(symbol=sym)
            add_rejection(a, "analysis_error", f"خطأ في التحليل: {exc}")
        results.append(a)
        if progress:
            try:
                progress(i + 1, len(symbols), sym)
            except Exception:  # noqa: BLE001
                pass
    results.sort(key=lambda x: (-x.phase, -x.score))
    return results
