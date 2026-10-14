"""النماذج الفنية — Technical pattern recognition tailored to the RS-reversal playbook.

النماذج المدعومة (كلها مناسبة لسيناريو: تقسيم عكسي ← هبوط قوي ← قاع ← انعكاس):
  1. وتد هابط (Falling Wedge)
  2. قناة هابطة واختراق سقفها (Descending Channel Break)
  3. قاع مزدوج (Double Bottom / W)
  4. قاعدة تجميع مع جفاف حجم (Base / Accumulation)
  5. علم صاعد (Bull Flag)
  6. كوب وعروة (Cup & Handle)
  7. بنية قيعان صاعدة (Higher Low Structure)
  8. شموع انعكاسية عند القاع (Hammer / Engulfing / Morning Star)
  9. جفاف الحجم ثم انفجار (Volume Dry-up → Expansion)
  10. ديفرجنس إيجابي RSI
  11. كسر الفجوة السعرية يوم التقسيم (Gap-Fill Momentum)
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

import numpy as np
import pandas as pd

from . import indicators as ind


@dataclass
class Pattern:
    name: str
    name_ar: str
    tf: str
    confidence: float          # 0..1
    bias: str                  # bullish | bearish | neutral
    description_ar: str        # وصف تحليلي بالعربية
    levels: dict[str, float] | None = None
    status: str = "forming"    # forming | confirmed | invalidated

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _pivot_lines(df: pd.DataFrame, kind: str, lookback: int = 45):
    """ملاءمة خط اتجاه على آخر القمم/القيعان (في فضاء لوغاريتمي).

    Returns (slope_per_bar, intercept, r2, n_points, last_value_at_bar_i)
    """
    swings = ind.swing_points(df, left=2, right=2)
    col = "swing_high" if kind == "high" else "swing_low"
    sub = df.tail(lookback)
    pts = swings[col].reindex(sub.index).dropna()
    if len(pts) < 2:
        return None
    x = np.array([sub.index.get_loc(i) for i in pts.index], dtype=float)
    y = np.log(pts.to_numpy(dtype=float))
    if len(x) < 2 or np.all(x == x[0]):
        return None
    slope, intercept = np.polyfit(x, y, 1)
    pred = slope * x + intercept
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum()) or 1e-9
    r2 = max(0.0, 1.0 - ss_res / ss_tot)
    x_last = float(len(sub) - 1)
    return slope, intercept, r2, len(pts), float(np.exp(slope * x_last + intercept))


# --------------------------------------------------------------------------- #
def falling_wedge(df: pd.DataFrame, tf: str = "1D", lookback: int = 45) -> Pattern | None:
    up = _pivot_lines(df, "high", lookback)
    dn = _pivot_lines(df, "low", lookback)
    if not up or not dn:
        return None
    up_slope, _, up_r2, n_up, up_last = up
    dn_slope, _, dn_r2, n_dn, dn_last = dn
    if up_slope >= 0 or dn_slope >= 0:          # يجب أن يكون السقف والقاع هابطين
        return None
    if n_up + n_dn < 4:
        return None
    width_now = (up_last - dn_last) / dn_last
    width_start = (np.exp(up[1]) - np.exp(dn[1])) / np.exp(dn[1])
    converging = width_now < width_start * 0.75
    if not converging:
        return None
    close = float(df["close"].iloc[-1])
    break_now = close > up_last * 1.005
    conf = float(np.clip(0.45 + 0.25 * min(1.0, (up_r2 + dn_r2) / 2) + (0.2 if break_now else 0.0), 0, 0.95))
    desc = (
        f"وتد هابط يتشكل من {n_up} قمة و{n_dn} قاع؛ السقف ينحدر "
        f"{abs(np.exp(up_slope)-1)*100:.2f}% للشمعة والقاع {abs(np.exp(dn_slope)-1)*100:.2f}%، "
        + ("والسعر اخترق سقف الوتد فعلياً → انعكاس مؤكد."
           if break_now else
           f"والسعر ما زال داخل الوتد وسقفه عند {up_last:.3f} — الاختراق فوقه هو الإشارة.")
    )
    return Pattern(
        name="falling_wedge", name_ar="وتد هابط", tf=tf,
        confidence=conf, bias="bullish", description_ar=desc,
        levels={"resistance": round(up_last, 4), "support": round(dn_last, 4)},
        status="confirmed" if break_now else "forming",
    )


def descending_channel_break(df: pd.DataFrame, tf: str = "1D", lookback: int = 40) -> Pattern | None:
    rc = ind.regression_channel(df, min(lookback, max(10, len(df) - 1)))
    if rc is None or rc["slope_pct_per_bar"].dropna().empty:
        return None
    last = rc.dropna().iloc[-1] if not rc.dropna().empty else None
    if last is None or last["slope_pct_per_bar"] >= -0.05:
        return None
    close = float(df["close"].iloc[-1])
    slope = float(last["slope_pct_per_bar"])
    upper = float(last["upper"])
    broke = close > upper
    dist = (close / upper - 1) * 100
    conf = float(np.clip(0.5 + (0.35 if broke else 0.0) + min(0.15, abs(slope) / 3), 0, 0.95))
    desc = (
        f"قناة هابطة بانحدار {slope:.2f}% للشمعة (30 شمعة). "
        + (f"السعر اخترق سقف القناة ({upper:.3f}) بفارق {dist:.1f}% → تحوّل الاتجاه قصير المدى إيجابي."
           if broke else
           f"سقف القناة عند {upper:.3f} والسعر أسفله بفارق {abs(dist):.1f}% — ننتظر اختراقاً بإغلاق.")
    )
    return Pattern(
        name="descending_channel", name_ar="قناة هابطة", tf=tf, confidence=conf,
        bias="bullish" if broke else "bearish", description_ar=desc,
        levels={"channel_upper": round(upper, 4), "channel_mid": round(float(last["mid"]), 4),
                "channel_lower": round(float(last["lower"]), 4)},
        status="confirmed" if broke else "forming",
    )


def double_bottom(df: pd.DataFrame, tf: str = "1D", lookback: int = 45, tol: float = 0.06) -> Pattern | None:
    sub = df.tail(lookback)
    swings = ind.swing_points(sub, left=2, right=2)
    lows = swings["swing_low"].dropna()
    if len(lows) < 2:
        return None
    pairs = list(zip(lows.index, lows.to_numpy()))[-3:]
    for i in range(len(pairs) - 1):
        d1, p1 = pairs[i]
        d2, p2 = pairs[i + 1]
        if d1 == d2:
            continue
        if abs(p2 - p1) / min(p1, p2) > tol:
            continue
        between = sub.loc[d1:d2, "high"]
        if between.empty:
            continue
        neck = float(between.max())
        rise = (neck - min(p1, p2)) / min(p1, p2) * 100
        if rise < 8:
            continue
        close = float(df["close"].iloc[-1])
        broken = close > neck
        conf = float(np.clip(0.5 + (0.3 if broken else 0.0) + (0.1 if rise > 15 else 0), 0, 0.95))
        desc = (
            f"قاع مزدوج: قاع {p1:.3f} بتاريخ {pd.Timestamp(d1).date()} وقاع {p2:.3f} "
            f"بتاريخ {pd.Timestamp(d2).date()} بفارق {abs(p2-p1)/min(p1,p2)*100:.1f}%، "
            f"وخط العنق عند {neck:.3f} ({rise:.1f}% فوق القاع). "
            + ("تم اختراق خط العنق → النموذج مكتمل." if broken
               else f"ننتظر إغلاقاً فوق {neck:.3f} لتأكيد الاختراق.")
        )
        return Pattern(
            name="double_bottom", name_ar="قاع مزدوج (W)", tf=tf, confidence=conf,
            bias="bullish", description_ar=desc,
            levels={"bottom1": round(float(p1), 4), "bottom2": round(float(p2), 4),
                    "neckline": round(neck, 4)},
            status="confirmed" if broken else "forming",
        )
    return None


def base_accumulation(
    df: pd.DataFrame, tf: str = "1D", ref_low: float | None = None,
    tolerance: float = 0.09, sessions: int = 3,
) -> Pattern | None:
    if len(df) < sessions + 5:
        return None
    sub = df.tail(sessions + 5)
    window = sub.tail(sessions + 2)
    lo = ref_low if ref_low is not None else float(window["low"].min())
    hi = float(window["high"].max())
    rng = (hi - lo) / lo * 100 if lo else 999
    if rng > tolerance * 100 * 1.6:
        return None
    vol_recent = float(window["volume"].tail(3).mean())
    vol_before = float(sub["volume"].head(max(3, len(sub) - 3)).mean()) or 1.0
    dry = vol_recent / vol_before
    atr_now = float(df["atr_pct"].iloc[-1]) if "atr_pct" in df else np.nan
    atr_prev = float(df["atr_pct"].iloc[-min(len(df) - 1, sessions + 5)]) if "atr_pct" in df else np.nan
    comp = atr_now / atr_prev if atr_prev and not np.isnan(atr_prev) else 1.0
    conf = float(np.clip(0.45 + (0.2 if dry < 0.85 else 0) + (0.2 if comp < 0.85 else 0)
                         + (0.1 if rng < tolerance * 100 * 0.7 else 0), 0, 0.95))
    desc = (
        f"قاعدة تجميع على مدى {sessions} جلسات داخل نطاق {rng:.1f}% "
        f"(القاع {lo:.3f} / القمة {hi:.3f}). حجم آخر 3 جلسات يساوي {dry*100:.0f}% من "
        f"متوسط ما قبله (جفاف بيعي) والتقلب ATR تغيّر بنسبة {(comp-1)*100:+.0f}%."
    )
    return Pattern(
        name="base", name_ar="قاعدة تجميع / ثبات على القاع", tf=tf, confidence=conf,
        bias="neutral", description_ar=desc,
        levels={"base_low": round(lo, 4), "base_high": round(hi, 4)}, status="forming",
    )


def bull_flag(df: pd.DataFrame, tf: str = "1D", lookback: int = 25) -> Pattern | None:
    sub = df.tail(lookback)
    if len(sub) < 12:
        return None
    closes = sub["close"].to_numpy(dtype=float)
    low_i = int(np.argmin(closes[: len(closes) // 2]))
    high_i = int(np.argmax(closes))
    if high_i <= low_i:
        return None
    pole = (closes[high_i] - closes[low_i]) / closes[low_i] * 100
    if pole < 12:
        return None
    flag = sub.iloc[high_i:]
    if len(flag) < 3:
        return None
    f_hi, f_lo = float(flag["high"].max()), float(flag["low"].min())
    retrace = (closes[high_i] - float(flag["close"].iloc[-1])) / (closes[high_i] - closes[low_i]) * 100
    if retrace > 60 and f_lo < closes[low_i]:
        return None
    flag_range = (f_hi - f_lo) / f_lo * 100
    conf = float(np.clip(0.4 + min(0.3, pole / 100) + (0.15 if retrace < 40 else 0), 0, 0.9))
    desc = (
        f"علم صاعد: سارية بارتفاع {pole:.0f}% من {closes[low_i]:.3f} إلى {closes[high_i]:.3f} "
        f"ثم تصحيح مضبوط بنطاق {flag_range:.1f}% وارتداد {retrace:.0f}% من السارية. "
        f"اختراق {f_hi:.3f} يفتح استكمال السارية."
    )
    return Pattern(
        name="bull_flag", name_ar="علم صاعد", tf=tf, confidence=conf, bias="bullish",
        description_ar=desc,
        levels={"flag_high": round(f_hi, 4), "flag_low": round(f_lo, 4),
                "pole_target": round(f_hi + (closes[high_i] - closes[low_i]), 4)},
        status="forming",
    )


def cup_and_handle(df: pd.DataFrame, tf: str = "4H", lookback: int = 60) -> Pattern | None:
    sub = df.tail(lookback)
    if len(sub) < 30:
        return None
    closes = sub["close"].to_numpy(dtype=float)
    n = len(closes)
    left_i, right_i = int(np.argmax(closes[: n // 3])), int(np.argmax(closes[-n // 3:]) + n - n // 3)
    low_i = int(np.argmin(closes[left_i:right_i + 1])) + left_i
    if not (left_i < low_i < right_i):
        return None
    rim = min(closes[left_i], closes[right_i])
    depth = (rim - closes[low_i]) / rim * 100
    if depth < 10 or depth > 60:
        return None
    if abs(closes[left_i] - closes[right_i]) / rim > 0.10:
        return None
    handle = sub.iloc[right_i:]
    if len(handle) < 3:
        return None
    h_retr = (rim - float(handle["low"].min())) / rim * 100
    if h_retr > depth * 0.6:
        return None
    close = float(df["close"].iloc[-1])
    conf = float(np.clip(0.45 + (0.25 if close > rim * 0.98 else 0) + (0.1 if h_retr < depth * 0.35 else 0), 0, 0.9))
    desc = (
        f"كوب وعروة: كوب بعمق {depth:.1f}% وحافتان متقاربتان عند {rim:.3f}، "
        f"وعروة بارتداد {h_retr:.1f}% فقط (ضغط عرض شحيح). "
        + ("السعر يقترب من حافة الكوب → اختراق وشيك." if close > rim * 0.98 else
           f"الاختراق المعتمد فوق {rim:.3f}.")
    )
    return Pattern(
        name="cup_handle", name_ar="كوب وعروة", tf=tf, confidence=conf, bias="bullish",
        description_ar=desc, levels={"rim": round(rim, 4), "bottom": round(closes[low_i], 4),
                                     "measure_target": round(rim + (rim - closes[low_i]), 4)},
        status="confirmed" if close > rim else "forming",
    )


def higher_low_structure(df: pd.DataFrame, since, tf: str = "1D") -> Pattern | None:
    swings = ind.swing_points(df, left=1, right=1)
    lows = swings["swing_low"].dropna()
    lows = lows.loc[lows.index >= since] if len(lows) else lows
    vals = lows.to_list()
    if len(vals) < 2:
        return None
    ups = [(a, b) for a, b in zip(vals, vals[1:]) if b > a * 1.01]
    if not ups:
        return None
    conf = float(np.clip(0.4 + 0.15 * min(3, len(ups)), 0, 0.9))
    desc = (
        f"بنية قيعان صاعدة: {len(ups)} قاع صاعد منذ بداية القاعدة "
        f"({vals[0]:.3f} → {vals[-1]:.3f}) وهو أول دليل على انتقال العرض إلى الطلب."
    )
    return Pattern(
        name="higher_lows", name_ar="بنية قيعان صاعدة", tf=tf, confidence=conf,
        bias="bullish", description_ar=desc,
        levels={"first_low": round(float(vals[0]), 4), "last_low": round(float(vals[-1]), 4)},
        status="confirmed",
    )


def candlestick_reversal(df: pd.DataFrame, tf: str = "1D", lookback: int = 6) -> Pattern | None:
    if len(df) < 3:
        return None
    sub = df.tail(lookback)
    found: list[tuple[str, str, float]] = []
    for i in range(len(sub)):
        row = sub.iloc[i]
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        rng = max(h - l, 1e-9)
        body = abs(c - o)
        upper = h - max(o, c)
        lower = min(o, c) - l
        loc = i + len(df) - len(sub)
        is_low_zone = loc >= len(df) - lookback
        if body / rng < 0.3 and lower / rng > 0.55 and is_low_zone:
            found.append(("hammer", "شمعة المطرقة", row.name))
        if i > 0:
            p = sub.iloc[i - 1]
            po, pc = float(p["open"]), float(p["close"])
            if pc < po and c > o and c >= po and o <= pc:
                found.append(("engulfing", "ابتلاع شرائي", row.name))
        if i > 1:
            a, b = sub.iloc[i - 2], sub.iloc[i - 1]
            if (float(a["close"]) < float(a["open"]) and abs(float(b["close"]) - float(b["open"])) / max(float(b["high"]) - float(b["low"]), 1e-9) < 0.35
                    and c > o and c > (float(a["open"]) + float(a["close"])) / 2):
                found.append(("morning_star", "نجمة الصباح", row.name))
    if not found:
        return None
    names_ar = {"hammer": "شمعة المطرقة", "engulfing": "ابتلاع شرائي", "morning_star": "نجمة الصباح"}
    uniq: dict[str, Any] = {}
    for k, ar, d in found:
        uniq[k] = (ar, d)
    labels = "، ".join(v[0] for v in uniq.values())
    dates = "، ".join(str(pd.Timestamp(v[1]).date()) for v in uniq.values())
    conf = float(np.clip(0.4 + 0.15 * len(uniq), 0, 0.85))
    return Pattern(
        name="candlestick_reversal", name_ar=f"شموع انعكاسية ({labels})", tf=tf, confidence=conf,
        bias="bullish",
        description_ar=f"ظهرت شموع انعكاسية إيجابية عند منطقة القاع بتاريخ {dates} — دليل دخول مشترين.",
        levels=None, status="confirmed",
    )


def volume_dryup_expansion(df: pd.DataFrame, tf: str = "1D", lookback: int = 20) -> Pattern | None:
    if len(df) < lookback:
        return None
    sub = df.tail(lookback)
    base_vol = float(sub["volume"].head(lookback // 2).mean()) or 1.0
    dry_min = float(sub["volume"].tail(lookback // 2).min()) / base_vol
    last_rvol = float(df["rvol"].iloc[-1]) if "rvol" in df and not pd.isna(df["rvol"].iloc[-1]) else 1.0
    if dry_min > 0.75:
        return None
    expanded = last_rvol >= 1.5
    conf = float(np.clip(0.45 + (0.3 if expanded else 0.05) + (0.15 if dry_min < 0.5 else 0), 0, 0.9))
    desc = (
        f"جفاف الحجم: أدنى حجم في النصف الأخير من النافذة = {dry_min*100:.0f}% من متوسط ما قبله "
        + (f"، ثم انفجار حجمي الآن بحجم نسبي {last_rvol:.1f}x → دخول مؤسسي محتمل."
           if expanded else "، ولم يحدث بعد انفجار حجمي (ننتظر تأكيد السيولة).")
    )
    return Pattern(
        name="volume_dryup", name_ar="جفاف حجم ثم انفجار", tf=tf, confidence=conf,
        bias="bullish" if expanded else "neutral", description_ar=desc,
        levels=None, status="confirmed" if expanded else "forming",
    )


def rsi_divergence_pattern(df: pd.DataFrame, tf: str = "1D") -> Pattern | None:
    if "rsi" not in df:
        return None
    ok = ind.bullish_divergence(df, df["rsi"], lookback=40)
    if not ok:
        return None
    desc = "ديفرجنس إيجابي على RSI: السعر سجّل قاعاً أدنى بينما RSI سجّل قاعاً أعلى → تلاشي ضغط البيع."
    return Pattern(name="rsi_divergence", name_ar="ديفرجنس إيجابي RSI", tf=tf,
                   confidence=0.7, bias="bullish", description_ar=desc, status="confirmed")


def detect_all(df: pd.DataFrame, tf: str, ctx: dict | None = None) -> list[Pattern]:
    """تشغيل كل النماذج على فريم معين. ctx يحمل معلومات التقسيم/القاع."""
    ctx = ctx or {}
    out: list[Pattern] = []
    checks = [
        lambda: falling_wedge(df, tf),
        lambda: descending_channel_break(df, tf),
        lambda: double_bottom(df, tf),
        lambda: bull_flag(df, tf),
        lambda: cup_and_handle(df, tf),
        lambda: candlestick_reversal(df, tf),
        lambda: volume_dryup_expansion(df, tf),
        lambda: rsi_divergence_pattern(df, tf),
    ]
    for fn in checks:
        try:
            p = fn()
        except Exception:  # noqa: BLE001 - نموذج واحد فاشل لا يجب أن يوقف التحليل
            p = None
        if p:
            out.append(p)

    ref_low = ctx.get("ref_low")
    sessions = int(ctx.get("base_sessions") or 3)
    try:
        bp = base_accumulation(df, tf, ref_low=ref_low, sessions=max(3, sessions))
        if bp:
            out.append(bp)
    except Exception:  # noqa: BLE001
        pass
    since = ctx.get("low_date") or ctx.get("rs_date")
    if since is not None:
        try:
            hp = higher_low_structure(df, since, tf)
            if hp:
                out.append(hp)
        except Exception:  # noqa: BLE001
            pass
    return sorted(out, key=lambda p: p.confidence, reverse=True)
