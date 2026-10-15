"""
strategy_engine.py
تطبيق استراتيجية: قاع ثانٍ / سحب سيولة + منطقة الهبوط (القمة ÷ 2) + أهداف 4×25%
نظام درجات (Scoring) بدل الاستبعاد الكلي (AND).

الاستخدام:
    from strategy_engine import evaluate_symbol, StrategyConfig
    result = evaluate_symbol(df, short_interest=15000, has_news=True)
    # df: DataFrame أعمدته open, high, low, close, volume (يومي، الأقدم أولًا)
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional
import numpy as np
import pandas as pd


@dataclass
class StrategyConfig:
    # --- السعر (شرط 3): فلتر صلب واسع + نقاط للنطاق المثالي ---
    price_ideal_min: float = 1.50
    price_ideal_max: float = 2.00
    price_hard_min: float = 0.10      # أقل منه يُستبعد (بيانات غير موثوقة)
    price_hard_max: float = 10.00     # أعلى منه يُستبعد (ليس بني ستوك)

    # --- الشورت (شرط 1) ---
    short_interest_max: int = 20_000  # أسهم؛ None/غير متوفر => محايد وليس استبعاد

    # --- المتوسطات (شرط 4) ---
    ma_periods: tuple = (20, 30, 50)

    # --- منطقة الهبوط (شرط 2): القمة ÷ divisor ---
    spike_lookback: int = 60
    drop_divisor: float = 2.0
    drop_zone_tolerance: float = 0.15  # ±15%

    # --- النمط: القاع الأول/الثاني/سحب السيولة (شرط 6-7) ---
    pattern_lookback: int = 90
    swing_window: int = 3              # عدد الشموع يمين/يسار لتحديد القاع
    second_low_tolerance: float = 0.04 # القاع الثاني ضمن ±4% من الأول
    min_bounce_pct: float = 0.15       # ارتداد لا يقل عن 15% بين القاعين
    min_bars_between_lows: int = 5

    # --- السلوك السلبي العام (شرط 5) ---
    negative_lookback: int = 90
    min_drawdown: float = 0.40         # هبوط 40%+ من أعلى 90 يوم

    # --- المقاومة (شرط 6) ---
    min_resistance_room: float = 0.20  # مقاومة لا تقل عن 20% فوق السعر

    # --- الأهداف والوقف (شرط 9) ---
    target_step: float = 0.25          # كل هدف +25%
    n_targets: int = 4
    stop_buffer: float = 0.03          # الوقف 3% تحت القاع الثاني
    risk_per_trade: float = 50.0       # مبلغ المخاطرة بالدولار (عدّله)

    # --- أوزان الدرجات (المجموع 100) ---
    weights: dict = field(default_factory=lambda: {
        "pattern": 30,     # قاع ثانٍ / سحب سيولة
        "confirm": 15,     # إغلاق فوق القاع + شمعة تأكيد
        "drop_zone": 15,   # قرب القمة÷2
        "price": 10,       # نطاق 1.5–2
        "short": 8,        # شورت < 20 ألف
        "ma": 8,           # المتوسطات
        "negative": 5,     # سلوك سلبي سابق
        "resistance": 5,   # مجال 20%+
        "news": 4,         # خبر/إعلان
    })

    ready_score: int = 75
    near_score: int = 55
    watch_score: int = 35


# ----------------------------------------------------------------- helpers
def _swing_lows(low: pd.Series, w: int) -> list[int]:
    idx = []
    v = low.values
    for i in range(w, len(v) - w):
        win = v[i - w:i + w + 1]
        if v[i] == win.min() and (win == v[i]).sum() == 1:
            idx.append(i)
    return idx


def _find_pattern(df: pd.DataFrame, cfg: StrategyConfig) -> dict:
    """يبحث عن: قاع أول -> ارتداد (اختبار مقاومة) -> قاع ثانٍ أو سحب سيولة."""
    d = df.tail(cfg.pattern_lookback).reset_index(drop=True)
    out = {"found": False, "type": None}
    lows = _swing_lows(d["low"], cfg.swing_window)
    # أحدث أزواج أولًا
    for j in range(len(lows) - 1, 0, -1):
        i2 = lows[j]
        for k in range(j - 1, -1, -1):
            i1 = lows[k]
            if i2 - i1 < cfg.min_bars_between_lows:
                continue
            l1, l2 = d.loc[i1, "low"], d.loc[i2, "low"]
            neck = d.loc[i1:i2, "high"].max()
            bounce = (neck - l1) / l1 if l1 > 0 else 0
            if bounce < cfg.min_bounce_pct:
                continue
            diff = (l2 - l1) / l1
            sweep = l2 < l1 and d.loc[i2:, "close"].max() >= l1 and \
                    d.loc[i2, "close"] >= l1 * (1 - cfg.second_low_tolerance)
            held = abs(diff) <= cfg.second_low_tolerance
            if held or sweep:
                out.update(found=True, type="liquidity_sweep" if sweep and l2 < l1 else "double_bottom",
                           low1=float(l1), low2=float(l2), neckline=float(neck),
                           low2_idx=int(i2), bars_since_low2=int(len(d) - 1 - i2))
                return out
    return out


def _confirmation(df: pd.DataFrame, pat: dict) -> dict:
    """إغلاق فوق القاع الثاني + شمعة خضراء تكسر قمة الشمعة السابقة (نقطة الدخول)."""
    if not pat["found"]:
        return {"confirmed": False}
    last, prev = df.iloc[-1], df.iloc[-2]
    green = last["close"] > last["open"]
    above_low = last["close"] > pat["low2"] * 1.0
    above_low1 = last["close"] >= pat["low1"]
    broke_prev = last["close"] > prev["high"]
    confirmed = bool(green and above_low and above_low1 and broke_prev
                     and pat["bars_since_low2"] <= 10)
    return {"confirmed": confirmed, "entry": float(max(last["high"], prev["high"])) if confirmed else None,
            "green": bool(green), "above_low1": bool(above_low1), "broke_prev_high": bool(broke_prev)}


def _drop_zone(df: pd.DataFrame, cfg: StrategyConfig) -> dict:
    """القمة ÷ 2: مثال افتتح 3 ووصل 3.40 => منطقة 1.70."""
    d = df.tail(cfg.spike_lookback)
    spike = ((d["high"] / d["open"]) - 1).idxmax()
    spike_high = float(d.loc[spike, "high"])
    zone = spike_high / cfg.drop_divisor
    lo, hi = zone * (1 - cfg.drop_zone_tolerance), zone * (1 + cfg.drop_zone_tolerance)
    return {"spike_high": spike_high, "zone": zone, "zone_low": lo, "zone_high": hi}


def _targets(entry: float, low2: float, cfg: StrategyConfig) -> dict:
    stop = low2 * (1 - cfg.stop_buffer)
    risk = entry - stop
    shares = int(cfg.risk_per_trade / risk) if risk > 0 else 0
    t = [round(entry * (1 + cfg.target_step * (i + 1)), 4) for i in range(cfg.n_targets)]
    return {"entry": round(entry, 4), "stop": round(stop, 4), "targets": t,
            "position_size": shares, "risk_per_share": round(risk, 4)}


# ----------------------------------------------------------------- main
def evaluate_symbol(df: pd.DataFrame, short_interest: Optional[float] = None,
                    has_news: Optional[bool] = None,
                    cfg: Optional[StrategyConfig] = None) -> dict:
    cfg = cfg or StrategyConfig()
    w = cfg.weights
    reasons_fail, reasons_ok, score = [], [], 0.0
    res = {"status": "EXCLUDED", "score": 0, "passed": [], "failed": [], "unknown": []}

    # ---- فلاتر صلبة (بيانات/نوع السهم فقط) ----
    if df is None or len(df) < 60:
        res["failed"].append("بيانات غير كافية (أقل من 60 شمعة)")
        return res
    df = df.dropna(subset=["open", "high", "low", "close"]).copy()
    price = float(df["close"].iloc[-1])
    res["price"] = price
    if not (cfg.price_hard_min <= price <= cfg.price_hard_max):
        res["failed"].append(f"السعر {price:.2f} خارج نطاق البني ستوك ({cfg.price_hard_min}-{cfg.price_hard_max})")
        return res

    # ---- السعر 1.5–2 ----
    if cfg.price_ideal_min <= price <= cfg.price_ideal_max:
        score += w["price"]; res["passed"].append("السعر ضمن 1.50–2.00")
    elif price < 5:
        score += w["price"] * 0.5; res["passed"].append("السعر بني ستوك لكن خارج 1.5–2 (نقاط جزئية)")
    else:
        res["failed"].append("السعر أعلى من 5 (لا نقاط)")

    # ---- الشورت ----
    if short_interest is None or (isinstance(short_interest, float) and np.isnan(short_interest)):
        score += w["short"] * 0.5; res["unknown"].append("الشورت غير متوفر (محايد)")
    elif short_interest < cfg.short_interest_max:
        score += w["short"]; res["passed"].append(f"الشورت {int(short_interest):,} < {cfg.short_interest_max:,}")
    else:
        res["failed"].append(f"الشورت {int(short_interest):,} ≥ {cfg.short_interest_max:,}")

    # ---- المتوسطات 20/30/50 ----
    for p in cfg.ma_periods:
        df[f"sma{p}"] = df["close"].rolling(p).mean()
    s20, s30, s50 = (df[f"sma{p}"].iloc[-1] for p in cfg.ma_periods)
    ma_pts = 0
    if price >= s20: ma_pts += 0.5
    if df["sma20"].iloc[-1] > df["sma20"].iloc[-3]: ma_pts += 0.25   # ميل MA20 للأعلى
    if price >= s30: ma_pts += 0.25
    score += w["ma"] * ma_pts
    (res["passed"] if ma_pts >= 0.5 else res["failed"]).append(
        f"المتوسطات: السعر {'فوق' if price >= s20 else 'تحت'} MA20 (MA20={s20:.2f}, MA30={s30:.2f}, MA50={s50:.2f})")

    # ---- السلوك السلبي السابق ----
    hh = df["high"].tail(cfg.negative_lookback).max()
    dd = 1 - price / hh if hh else 0
    ll = df["low"].tail(cfg.negative_lookback).min()
    dd_from_high_to_low = 1 - ll / hh if hh else 0
    if dd_from_high_to_low >= cfg.min_drawdown:
        score += w["negative"]; res["passed"].append(f"سلوك سلبي سابق: هبوط {dd_from_high_to_low:.0%} من القمة")
    else:
        res["failed"].append(f"لا هبوط كافٍ سابقًا ({dd_from_high_to_low:.0%})")

    # ---- منطقة الهبوط: القمة ÷ 2 ----
    dz = _drop_zone(df, cfg); res["drop_zone"] = dz
    ref = None
    # نقيس قرب القاع الثاني (إن وجد) أو السعر الحالي من المنطقة
    pat = _find_pattern(df, cfg); res["pattern"] = pat
    ref = pat["low2"] if pat["found"] else price
    if dz["zone_low"] <= ref <= dz["zone_high"]:
        score += w["drop_zone"]; res["passed"].append(f"القاع/السعر {ref:.2f} داخل منطقة القمة÷2 ({dz['zone']:.2f})")
    elif dz["zone_low"] * 0.8 <= ref <= dz["zone_high"] * 1.2:
        score += w["drop_zone"] * 0.5; res["passed"].append(f"قريب من منطقة القمة÷2 ({dz['zone']:.2f}) (جزئي)")
    else:
        res["failed"].append(f"بعيد عن منطقة القمة÷2 ({dz['zone']:.2f})")

    # ---- النمط + التأكيد ----
    conf = _confirmation(df, pat) if pat["found"] else {"confirmed": False}
    res["confirmation"] = conf
    if pat["found"]:
        score += w["pattern"]
        label = "سحب سيولة" if pat["type"] == "liquidity_sweep" else "قاع ثانٍ (دعم ثابت)"
        res["passed"].append(f"نمط: {label} — قاع1={pat['low1']:.2f}, قاع2={pat['low2']:.2f}")
    else:
        res["failed"].append("لم يظهر قاع ثانٍ/سحب سيولة بعد")
    if conf["confirmed"]:
        score += w["confirm"]; res["passed"].append("إغلاق فوق القاع + شمعة تأكيد تكسر القمة السابقة")
    elif pat["found"]:
        res["failed"].append("بانتظار شمعة التأكيد (إغلاق أخضر يكسر قمة الشمعة السابقة)")

    # ---- مجال المقاومة 20%+ ----
    res_level = df["high"].tail(cfg.pattern_lookback).max()
    room = res_level / price - 1
    if room >= cfg.min_resistance_room:
        score += w["resistance"]; res["passed"].append(f"مجال صعود حتى المقاومة {room:.0%}")
    else:
        res["failed"].append(f"المقاومة قريبة ({room:.0%} فقط)")

    # ---- الأخبار ----
    if has_news is None:
        score += w["news"] * 0.5; res["unknown"].append("الأخبار غير متوفرة (محايد)")
    elif has_news:
        score += w["news"]; res["passed"].append("يوجد خبر/إعلان")
    else:
        res["failed"].append("لا يوجد خبر")

    # ---- الحالة النهائية ----
    res["score"] = round(score, 1)
    if score >= cfg.ready_score and conf["confirmed"]:
        res["status"] = "READY"          # جاهز للدخول
    elif score >= cfg.near_score and pat["found"]:
        res["status"] = "NEAR"           # قريب من الاكتمال
    elif score >= cfg.watch_score:
        res["status"] = "WATCH"          # مراقبة
    else:
        res["status"] = "EXCLUDED"
        res["failed"].append(f"الدرجة {score:.0f} أقل من حد المراقبة {cfg.watch_score}")

    if pat["found"]:
        entry = conf["entry"] if conf.get("entry") else price
        res["trade_plan"] = _targets(entry, pat["low2"], cfg)
    return res


STATUS_AR = {"READY": "جاهز للدخول", "NEAR": "قريب من الاكتمال",
             "WATCH": "مراقبة", "EXCLUDED": "مستبعد"}
