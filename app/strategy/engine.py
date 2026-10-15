"""محرّك التحليل — يربط بيانات السوق (DataHub) بمحرّك الاستراتيجية الحالي.

منطق القرار (الأهلية، الدرجة، المرحلة، خطة الصفقة) موجود في `strategy_engine.py`.
هذا الملف مسؤول عن:
  1) جلب البيانات من DataHub (أسعار يومية، ملف تعريفي، شورت، أخبار، تخفيف).
  2) تمرير السلسلة اليومية إلى محرّك الاستراتيجية.
  3) تحويل النتيجة إلى كائن `Analysis` الذي تعرضه الواجهة والتقارير.

المراحل (Phases):
  0 مستبعد · 2 مراقبة (WATCH) · 5 قريب من الاكتمال (NEAR) · 6 جاهز للدخول (READY)
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..analytics import indicators as ind
from ..analytics import patterns as pat
from ..providers.base import Catalyst, DilutionSignal, ShortData, TickerProfile
from ..providers.hub import DataHub
from .strategy_engine import STATUS_AR, StrategyConfig, evaluate_symbol

# --------------------------------------------------------------------------- #
#  المراحل
# --------------------------------------------------------------------------- #
PHASES: dict[int, dict[str, str]] = {
    0: {"key": "REJECTED", "ar": "مستبعد", "color": "#5b6472",
        "desc": "الدرجة أقل من حد المراقبة أو لم تتحقق الشروط الأساسية."},
    2: {"key": "WATCH", "ar": "المرحلة 2 — مراقبة", "color": "#e3b341",
        "desc": "الدرجة بين حد المراقبة وحد القرب، ولم يظهر نمط قاع ثانٍ أو سحب سيولة بعد."},
    5: {"key": "NEAR", "ar": "المرحلة 5 — قريب من الاكتمال", "color": "#2ea043",
        "desc": "ظهر نمط القاع الثاني أو سحب السيولة والدرجة فوق حد القرب، بانتظار شمعة التأكيد."},
    6: {"key": "READY", "ar": "المرحلة 6 — جاهز للدخول", "color": "#1f9a4d",
        "desc": "الدرجة فوق حد الجاهزية مع إغلاق فوق القاع وشمعة تأكيد تكسر قمة الشمعة السابقة."},
}

# ترتيب معايير الاستراتيجية كما تُعرض في الواجهة (مفتاح الدرجة، اسم عربي، مفتاح الخطوة)
CHECKLIST: list[tuple[str, str]] = [
    ("pattern", "قاع ثانٍ / سحب سيولة"),
    ("confirm", "شمعة تأكيد"),
    ("drop_zone", "منطقة القمة ÷ 2"),
    ("negative", "هبوط 40%+ بعد القمة"),
    ("price", "السعر 1.50–2.00"),
    ("resistance", "مجال المقاومة 20%+"),
]


def phase_info(pid: int) -> dict[str, str]:
    return PHASES.get(pid, PHASES[0])


def grade_for(score: float) -> str:
    if score >= 90: return "A+"
    if score >= 80: return "A"
    if score >= 70: return "B+"
    if score >= 60: return "B"
    if score >= 50: return "C"
    return "D"


# --------------------------------------------------------------------------- #
#  بنية النتيجة
# --------------------------------------------------------------------------- #
@dataclass
class Analysis:
    symbol: str
    name: str = ""
    sector: str = ""
    industry: str = ""
    exchange: str = ""
    data_source: str = "demo"
    price_source: str = ""
    fundamentals_source: str = ""
    news_source: str = ""
    synthetic: bool = True   # True = بيانات مولّدة/تجريبية، False = حقيقية مؤكدة
    as_of: str = ""
    last_close: float = 0.0
    change_pct: float = 0.0
    dollar_volume: float = 0.0
    atr_pct: float | None = None
    higher_lows: int = 0

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

    # -- نتيجة الاستراتيجية -- #
    strategy_status: str = "EXCLUDED"            # READY / NEAR / WATCH / EXCLUDED
    drop_zone: dict[str, Any] = field(default_factory=dict)
    pattern: dict[str, Any] = field(default_factory=dict)
    confirmation: dict[str, Any] = field(default_factory=dict)
    trade_plan: dict[str, Any] | None = None

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
    lifecycle_stage: str = ""
    lifecycle_ready: bool = False
    lifecycle_failed: bool = False
    lifecycle_checks: list[dict] = field(default_factory=list)
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
        info = phase_info(self.phase)
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


def _fmt_num(x) -> str:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "—"
    if abs(x) >= 1e9: return f"{x / 1e9:.2f}B"
    if abs(x) >= 1e6: return f"{x / 1e6:.2f}M"
    if abs(x) >= 1e3: return f"{x / 1e3:.1f}K"
    return f"{x:.0f}"


def sessions_ar(n) -> str:
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return "—"
    if n == 1: return "جلسة واحدة"
    if n == 2: return "جلستان"
    if 3 <= n <= 10: return f"{n} جلسات"
    return f"{n} جلسة"


def add_rejection(a: Analysis, code: str, reason: str) -> None:
    """يسجّل سبب استبعاد منظّماً (رمز + نص عربي)."""
    a.rejection_codes.append(str(code))
    a.rejections.append(str(reason))


# --------------------------------------------------------------------------- #
#  الفريم 4 ساعات (معلومات عرض فقط — لا تدخل في الدرجة)
# --------------------------------------------------------------------------- #
def analyse_4h(df4: pd.DataFrame) -> dict[str, Any]:
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
    out["aligned"] = bool(trend == "up" and rsi_now is not None and rsi_now > 30)
    return out


# --------------------------------------------------------------------------- #
#  ترجمة نتيجة المحرّك إلى حالة العرض
# --------------------------------------------------------------------------- #
def _apply_strategy_result(a: Analysis, res: dict[str, Any], df: pd.DataFrame) -> None:
    status = res.get("status", "EXCLUDED")
    a.strategy_status = status
    a.score = float(res.get("score") or 0.0)
    a.grade = grade_for(a.score)
    a.drop_zone = res.get("drop_zone") or {}
    a.pattern = res.get("pattern") or {}
    a.confirmation = res.get("confirmation") or {}
    a.trade_plan = res.get("trade_plan")

    # المرحلة والأهلية
    a.phase = {"READY": 6, "NEAR": 5, "WATCH": 2}.get(status, 0)
    a.display_phase = a.phase
    a.phase_key = phase_info(a.phase)["key"]
    a.phase_ar = phase_info(a.phase)["ar"]
    a.eligible = status in ("READY", "NEAR")
    a.watchlist = status in ("READY", "NEAR", "WATCH")
    a.lifecycle_ready = status == "READY"
    a.lifecycle_failed = bool(res.get("pattern", {}).get("broken"))
    a.lifecycle_stage = STATUS_AR.get(status, status)

    # قائمة المعايير (لعرض النقاط في الواجهة) — من أعلام المحرّك مباشرة
    crit = res.get("criteria", {})
    a.lifecycle_checks = [
        {"key": k, "ar": ar, "ok": bool(crit.get(k)),
         "state": "done" if crit.get(k) else "pending", "std": ar, "detail": ""}
        for k, ar in CHECKLIST
    ]

    # أسباب الاستبعاد: كل معيار فاشل عند الاستبعاد
    if status == "EXCLUDED":
        for text in res.get("failed", []):
            add_rejection(a, "criteria", text)
    elif res.get("failed"):
        a.notes.extend(res["failed"])

    a.notes.extend(res.get("unknown", []))
    a.confirmations = list(res.get("passed", []))

    # الدرجة التفصيلية
    a.breakdown = [
        {"key": b["key"], "ar": b["ar"], "score": b["score"], "weight": b["weight"],
         "points": b["points"], "detail": ""}
        for b in res.get("breakdown", [])
    ]

    # المستويات (خطة الصفقة من المحرّك)
    plan = res.get("trade_plan")
    if plan and plan.get("valid"):
        entry = plan["entry"]
        stop = plan["stop"]
        t = plan["targets"]
        a.levels = {
            "trigger": entry,
            "entry_zone": [_r(min(stop * 1.01, entry)), entry],
            "stop": stop,
            "stop_pct": _r(_pct(stop, entry), 2),
            "targets": {f"t{i + 1}": v for i, v in enumerate(t)},
            "target_step_pct": 25,
            "position_size": plan["position_size"],
            "risk_per_share": plan["risk_per_share"],
        }
    else:
        a.levels = {}

    low1 = a.pattern.get("low1")
    low2 = a.pattern.get("low2")
    a.levels.update({
        "bottom": _r(low2) if low2 else None,
        "pattern_low1": _r(low1) if low1 else None,
        "pattern_low2": _r(low2) if low2 else None,
        "neckline": _r(a.pattern.get("neckline")) if a.pattern.get("neckline") else None,
        "drop_zone": _r(a.drop_zone.get("zone")) if a.drop_zone else None,
        "drop_zone_low": _r(a.drop_zone.get("zone_low")) if a.drop_zone else None,
        "drop_zone_high": _r(a.drop_zone.get("zone_high")) if a.drop_zone else None,
        "last": a.last_close,
        "ema9": _r(df["ema9"].iloc[-1]) if "ema9" in df else None,
        "ema20": _r(df["ema20"].iloc[-1]) if "ema20" in df else None,
    })

    # السرد
    why = list(res.get("passed", []))
    a.why_ar = why
    action = {
        "READY": "جاهز للدخول وفق خطة الصفقة — اعتمد الوقف قبل الدخول.",
        "NEAR": "قريب من الاكتمال — راقب شمعة التأكيد قبل الدخول.",
        "WATCH": "مراقبة فقط — لم يكتمل النمط بعد.",
        "EXCLUDED": "لا دخول — المعايير غير مكتملة.",
    }.get(status, "")
    a.action_ar = action


# --------------------------------------------------------------------------- #
#  التحليل الرئيسي
# --------------------------------------------------------------------------- #
def analyze_symbol(symbol: str, hub: DataHub, cfg: StrategyConfig | None = None) -> Analysis:
    cfg = cfg or StrategyConfig()
    symbol = symbol.upper()
    a = Analysis(symbol=symbol)
    a.data_source = hub.effective_mode

    raw_daily = hub.daily(symbol, 320)
    if raw_daily is None or len(raw_daily) < 60:
        reason = hub.rejections.get(symbol, "لا توجد بيانات سعرية كافية")
        add_rejection(a, "missing_price_data", reason)
        a.synthetic = (hub.effective_mode == "demo")
        return a
    df = ind.add_indicators(raw_daily, None)

    prof: TickerProfile | None = hub.profile(symbol)
    if prof is None:
        reason = hub.rejections.get(symbol, "لا توجد بيانات حقيقية لهذا الرمز")
        add_rejection(a, "missing_profile_data", reason)
        a.synthetic = False
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

    is_live = hub.mode == "live"
    a.synthetic = not is_live
    a.price_source = "yahoo-finance" if is_live else "demo-generator"
    a.fundamentals_source = prof.source or ("yahoo-finance" if is_live else "demo-generator")

    a.last_close = _r(df["close"].iloc[-1]) or 0.0
    a.change_pct = _r(_pct(df["close"].iloc[-1], df["close"].iloc[-2]), 2) or 0.0
    a.as_of = _date_str(df.index[-1]) or ""
    a.dollar_volume = _r(df["dollar_vol"].iloc[-1], 0) or 0.0
    a.atr_pct = _r(df["atr_pct"].iloc[-1], 2) if "atr_pct" in df else None

    # ---------- البيع المكشوف ---------- #
    sd: ShortData | None = hub.short_data(symbol, a.float_shares)
    if sd is not None:
        a.short_pct_float = _r(sd.short_pct_float, 2)
        a.short_shares = _r(sd.short_shares, 0)
        a.days_to_cover = _r(sd.days_to_cover, 2)
        a.short_change_pct = _r(sd.change_pct, 1)
        a.short_volume_ratio = _r(sd.short_volume_ratio, 3)
        a.short_source = sd.source

    # ---------- الأخبار والمخاطر ---------- #
    cats: list[Catalyst] = hub.catalysts(symbol)
    a.catalysts = [c.to_dict() for c in cats]
    dil: list[DilutionSignal] = hub.dilution(symbol)
    a.dilution = [d.to_dict() for d in dil]
    if cats:
        sources_used = sorted({c.source for c in cats if c.source})
        a.news_source = "+".join(sources_used) if sources_used else a.data_source
    else:
        a.news_source = "" if is_live else "demo-generator"
    a.negative_news = [c.to_dict() for c in cats if c.sentiment == "negative"]
    a.positive_news = [c.to_dict() for c in cats if c.sentiment == "positive"]
    if not a.price_source:
        a.price_source = "yahoo-finance" if is_live else "demo-generator"
    if not a.fundamentals_source:
        a.fundamentals_source = "" if is_live else "demo-generator"

    hi_recent = [d for d in dil if d.severity == "high"]
    if hi_recent:
        a.risk_flags.append("⚠ طرح أسهم/تخفيف عالي الخطورة: " + " | ".join(d.title[:70] for d in hi_recent[:2]))
    if a.synthetic:
        a.risk_flags.append("ℹ البيانات المعروضة تجريبية (demo) — فعّل الوضع الحي من الإعدادات")

    # ---------- محرّك الاستراتيجية ---------- #
    res = evaluate_symbol(
        df,
        short_interest=a.short_shares,
        has_news=bool(cats),
        cfg=cfg,
    )
    _apply_strategy_result(a, res, df)
    if a.lifecycle_failed:
        a.risk_flags.append("⚠ كسر وقف النمط — السيناريو منتهٍ حتى يتكوّن قاع جديد")

    # ---------- النماذج الفنية ---------- #
    ctx = {"ref_low": a.levels.get("pattern_low2") or None,
           "base_sessions": 3,
           "low_date": None}
    try:
        a.patterns_daily = [p.to_dict() for p in pat.detect_all(df, "1D", ctx)]
    except Exception as exc:  # noqa: BLE001
        a.notes.append(f"تعذّر حساب النماذج الفنية: {str(exc)[:80]}")

    try:
        df4 = ind.add_indicators(hub.intraday_4h(symbol, 90), None)
        if len(df4) > 30:
            a.h4 = analyse_4h(df4)
            a.patterns_4h = [p.to_dict() for p in pat.detect_all(df4, "4H", {
                "ref_low": float(df4["low"].tail(20).min()),
                "base_sessions": 3,
                "low_date": df4.index[max(0, len(df4) - 20)],
            })]
    except Exception as exc:  # noqa: BLE001
        a.h4 = {"available": False, "error": str(exc)[:120]}

    a.note_ar = hub.scenario_note(symbol)

    # ---------- سلسلة زمنية للرسم ---------- #
    keep = ["open", "high", "low", "close", "volume", "ema9", "ema20", "ema50", "rsi",
            "macd", "macd_signal", "macd_hist", "bb_up", "bb_mid", "bb_low",
            "ch_up", "ch_mid", "ch_low", "rvol", "atr_pct", "stoch_k", "stoch_d", "mfi"]
    cols = [c for c in keep if c in df.columns]
    tail = df[cols].tail(180)
    a.series = {
        "daily": _series_to_json(tail),
        "markers": _pattern_markers(df, a.pattern, cfg),
    }
    return a


def _pattern_markers(df: pd.DataFrame, pattern: dict[str, Any], cfg: StrategyConfig) -> dict[str, Any]:
    """تواريخ القاعين على الرسم (فهارس النمط نسبية لآخر pattern_lookback شمعة)."""
    if not pattern.get("found"):
        return {"low1_date": None, "low2_date": None}
    offset = max(0, len(df) - cfg.pattern_lookback)
    def at(idx):
        try:
            return _date_str(df.index[offset + int(idx)])
        except (IndexError, TypeError, ValueError):
            return None
    return {"low1_date": at(pattern.get("low1_idx", -1)), "low2_date": at(pattern.get("low2_idx", -1))}


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
def scan(symbols: list[str], hub: DataHub, cfg: StrategyConfig | None = None,
         progress=None) -> list[Analysis]:
    results: list[Analysis] = []
    for i, sym in enumerate(symbols):
        try:
            a = analyze_symbol(sym, hub, cfg)
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
