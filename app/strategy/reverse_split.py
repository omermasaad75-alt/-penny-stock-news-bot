"""كشف التقسيم العكسي وقراءة بنيته السعرية (صعود يوم التقسيم، القمة، الهبوط).

مأخوذ من المنطق القديم (`engine_main.py`) ومُكيَّف للمحرّك الجديد:
- `pick_reverse_split`: اختيار أحدث تقسيم عكسي صالح داخل نافذة `rs_lookback_days`
  ونسبة بين `rs_min_ratio` و`rs_max_ratio`، مع أسباب رفض الأحداث الأخرى.
- `rs_structure`: من جلسة التقسيم تُحسب قمة الصعود (داخل `spike_window_sessions`)،
  ثم القاع المرجعي (أدنى قاع قبل «الانطلاقة» التالية) ونسبة الهبوط منه.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..config import STRATEGY, StrategyParams
from ..providers.base import ReverseSplitEvent


def _pct(a: float, b: float) -> float | None:
    try:
        return (float(a) / float(b) - 1.0) * 100.0 if b else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _r(x: Any, n: int = 2) -> float | None:
    try:
        return None if x is None else round(float(x), n)
    except (TypeError, ValueError):
        return None


def pick_reverse_split(events: list[ReverseSplitEvent] | None,
                       params: StrategyParams = STRATEGY,
                       as_of: pd.Timestamp | None = None
                       ) -> tuple[ReverseSplitEvent | None, list[str]]:
    """اختيار التقسيم العكسي الصالح داخل النافذة + أسباب رفض غيره."""
    if not events:
        return None, ["لا يوجد حدث تقسيم عكسي مسجّل لهذا الرمز"]
    as_of = pd.Timestamp(as_of or pd.Timestamp.now()).normalize()
    reasons: list[str] = []
    best: ReverseSplitEvent | None = None
    for ev in sorted(events, key=lambda e: e.date, reverse=True):
        age = int((as_of - pd.Timestamp(ev.date).normalize()).days)
        if age < 0:
            continue
        if age > params.rs_lookback_days:
            reasons.append(
                f"تقسيم عكسي عمره {age} يوماً ({ev.ratio_str}) — خارج نافذة "
                f"{params.rs_lookback_days} يوماً المطلوبة")
            continue
        if not (params.rs_min_ratio <= float(ev.factor) <= params.rs_max_ratio):
            reasons.append(
                f"نسبة التقسيم {ev.ratio_str} خارج النطاق المقبول "
                f"1:{params.rs_min_ratio:g} – 1:{params.rs_max_ratio:g}")
            continue
        if best is None:
            best = ev  # القائمة مرتبة تنازلياً، فأول صالح هو الأحدث
    return best, reasons


def rs_structure(df: pd.DataFrame, ev: ReverseSplitEvent,
                 params: StrategyParams = STRATEGY) -> dict[str, Any]:
    """بنية السعر بعد التقسيم: افتتاح يوم التقسيم، القمة، القاع المرجعي، الهبوط."""
    out: dict[str, Any] = {}
    rs_ts = pd.Timestamp(ev.date).normalize()
    after = df.index[df.index >= rs_ts]
    if len(after) == 0:
        after = df.index[-1:]
    rs_pos = int(df.index.get_loc(after[0]))
    rs_bar = df.iloc[rs_pos]
    rs_open = float(rs_bar["open"]) or float(rs_bar["close"])

    # قمة الصعود داخل النافذة (من جلسة التقسيم)
    end_pos = min(len(df), rs_pos + max(1, params.spike_window_sessions) + 1)
    seg_high = df["high"].iloc[rs_pos:end_pos].to_numpy(dtype=float)
    peak_pos = int(seg_high.argmax()) + rs_pos
    spike_high = float(df["high"].iloc[peak_pos])
    out.update(
        rs_open=_r(rs_open, 4),
        rs_spike_high=_r(spike_high, 4),
        rs_spike_pct=_r(_pct(spike_high, rs_open)),
        rs_spike_date=str(pd.Timestamp(df.index[peak_pos]).date()),
    )

    # القاع المرجعي = أدنى قاع قبل أول «انطلاقة» (ارتفاع لاحق ≥ الحد من ذلك القاع).
    # هذا يستثني سحب السيولة اللاحق من اعتباره قاعاً مرجعياً.
    lows = df["low"].iloc[peak_pos:].to_numpy(dtype=float)
    highs = df["high"].iloc[peak_pos:].to_numpy(dtype=float)
    n = len(lows)
    if n:
        suffix_max = np.full(n, -np.inf)
        for k in range(n - 2, -1, -1):
            suffix_max[k] = max(suffix_max[k + 1], highs[k + 1])
        target = 1 + params.min_ready_impulse_pct / 100.0
        qual = np.where((lows > 0) & (suffix_max >= lows * target))[0]
        if qual.size:
            bottom_pos = peak_pos + int(qual[np.argmin(lows[qual])])
        else:  # لا انطلاقة: أدنى قاع بعد القمة
            bottom_pos = peak_pos + int(lows.argmin())
    else:
        bottom_pos = peak_pos
    bottom = float(df["low"].iloc[bottom_pos])
    out.update(
        rs_bottom_price=_r(bottom, 4),
        rs_bottom_date=str(pd.Timestamp(df.index[bottom_pos]).date()),
        rs_peak_date=str(pd.Timestamp(df.index[peak_pos]).date()),
        rs_decline_pct=_r(_pct(bottom, spike_high)),
    )
    return out


def gam_policy(params: StrategyParams = STRATEGY, cfg: Any = None) -> dict[str, Any]:
    """وصف سياسة مرشح GAM كما تُعرض في الواجهة والتقارير."""
    from .strategy_engine import StrategyConfig  # استيراد متأخر لتفادي الدورة

    scfg = cfg or StrategyConfig()
    return {
        "rs_lookback_days": params.rs_lookback_days,
        "rs_ratio_range": [params.rs_min_ratio, params.rs_max_ratio],
        "price_range": [scfg.price_hard_min, scfg.price_hard_max],
        "decline_zone_pct": [params.min_drop_pct, round(scfg.min_drawdown * 100, 2)],
        "rsi_oversold": params.rsi_oversold,
        "rsi_scope": "أدنى RSI خلال آخر 40 جلسة",
        "historical_state_persisted": False,
        "description_ar": (
            "مرشح ثانوي للمراقبة فقط: تقسيم عكسي صالح خلال النافذة، ونمط قاع ثانٍ أو سحب سيولة، "
            "والإخفاق يقتصر على هبوط بين 30% و40% و/أو RSI لم يتشبّع. لا يُعدّ إشارة دخول."
        ),
    }
