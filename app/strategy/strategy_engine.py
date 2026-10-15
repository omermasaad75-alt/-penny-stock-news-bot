"""
strategy_engine.py — محرّك الاستراتيجية الحالي للنظام.

الاستراتيجية: قاع ثانٍ / سحب سيولة + منطقة الهبوط (القمة ÷ 2) + أهداف 4×25%.
نظام درجات (Scoring) بدل الاستبعاد الكلي (AND).

الاستخدام:
    from app.strategy.strategy_engine import evaluate_symbol, StrategyConfig
    result = evaluate_symbol(df, short_interest=15000, has_news=True)
    # df: DataFrame أعمدته open, high, low, close, volume (يومي، الأقدم أولًا)

التعديلات مقارنة بالنسخة الأولى:
  * حساب الهبوط (شرط 5) يُقاس من القمة إلى أدنى قاع **بعدها** فقط.
  * النمط يُعتبر منكسرًا إذا أُغلق السعر أسفل القاع الثاني بأكثر من هامش الوقف،
    وعندها لا تُحسب خطة صفقة ولا يُمنح نقاط النمط.
  * خطة الصفقة تُعاد بعلامة valid=False إذا كانت المخاطرة للسهم غير موجبة.
  * تُعاد تفصيلة الدرجات (breakdown) لكل معيار لعرضها في الواجهة.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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
    peak_lookback: int = 90            # نافذة القمة: أعلى سعر (high) خلال 90 شمعة — مصدر واحد للقمة
    drop_divisor: float = 2.0
    drop_zone_tolerance: float = 0.15  # ±15%

    # --- النمط: القاع الأول/الثاني/سحب السيولة (شرط 6-7) ---
    pattern_lookback: int = 90
    swing_window: int = 3              # عدد الشموع يمين/يسار لتحديد القاع
    second_low_tolerance: float = 0.04 # القاع الثاني ضمن ±4% من الأول
    min_bounce_pct: float = 0.15       # ارتداد لا يقل عن 15% بين القاعين
    min_bars_between_lows: int = 5

    # --- السلوك السلبي العام (شرط 5) ---
    min_drawdown: float = 0.40         # هبوط 40%+ من أعلى 90 يوم (من القمة إلى ما بعدها)

    # --- المقاومة (شرط 6) ---
    min_resistance_room: float = 0.20  # مقاومة لا تقل عن 20% فوق السعر

    # --- الأهداف والوقف (شرط 9) ---
    target_step: float = 0.25          # كل هدف +25%
    n_targets: int = 4
    stop_buffer: float = 0.03          # الوقف 3% تحت القاع الثاني
    min_risk_pct: float = 0.01         # أقل مسافة مخاطرة (% من الدخول) لاعتبار الخطة صالحة
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


WEIGHT_LABELS_AR = {
    "pattern": "قاع ثانٍ / سحب سيولة",
    "confirm": "إغلاق فوق القاع + شمعة تأكيد",
    "drop_zone": "قرب منطقة القمة ÷ 2",
    "price": "السعر ضمن النطاق",
    "short": "الشورت",
    "ma": "المتوسطات",
    "negative": "سلوك سلبي سابق",
    "resistance": "مجال المقاومة",
    "news": "خبر / إعلان",
}


# ----------------------------------------------------------------- helpers
def _swing_lows(low: pd.Series, w: int) -> list[int]:
    # قاع محوري: أدنى من كل الشموع الـ w السابقة، وأقل أو يساوي كل الشموع الـ w اللاحقة.
    # القيود غير المتماثلة تأخذ أول شمعة من القيم المتساوية (أسعار الأسهم الرخيصة تتكرر بسبب
    # التدرج 0.01، وقاعدة «قاع فريد» كانت تُسقط قيعانًا حقيقية).
    idx = []
    v = low.to_numpy(dtype=float)
    for i in range(w, len(v) - w):
        if np.all(v[i - w:i] > v[i]) and np.all(v[i + 1:i + w + 1] >= v[i]):
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
                           low1_idx=int(i1), low2_idx=int(i2),
                           bars_since_low2=int(len(d) - 1 - i2))
                return out
    return out


def _confirmation(df: pd.DataFrame, pat: dict) -> dict:
    """إغلاق فوق القاع الثاني + شمعة خضراء تكسر قمة الشمعة السابقة (نقطة الدخول)."""
    if not pat["found"]:
        return {"confirmed": False}
    last, prev = df.iloc[-1], df.iloc[-2]
    green = last["close"] > last["open"]
    above_low = last["close"] > pat["low2"]
    above_low1 = last["close"] >= pat["low1"]
    broke_prev = last["close"] > prev["high"]
    confirmed = bool(green and above_low and above_low1 and broke_prev
                     and pat["bars_since_low2"] <= 10)
    return {"confirmed": confirmed,
            "entry": float(max(last["high"], prev["high"])) if confirmed else None,
            "green": bool(green), "above_low1": bool(above_low1), "broke_prev_high": bool(broke_prev)}


def _peak(df: pd.DataFrame, cfg: StrategyConfig) -> tuple[int, float]:
    """القمة الحقيقية: أعلى سعر high داخل النافذة (وليس أكبر نسبة قفز من الافتتاح).

    يُستخدم المصدر نفسه لمنطقة القمة÷2 ولحساب الهبوط بعد القمة، حتى لا يختلفا.
    """
    highs = df["high"].tail(cfg.peak_lookback).to_numpy(dtype=float)
    pk = int(np.argmax(highs))
    return pk, float(highs[pk])


def _drop_zone(df: pd.DataFrame, cfg: StrategyConfig) -> dict:
    """القمة ÷ 2: مثال افتتح 3 ووصل 3.40 => منطقة 1.70."""
    _, spike_high = _peak(df, cfg)
    zone = spike_high / cfg.drop_divisor
    lo, hi = zone * (1 - cfg.drop_zone_tolerance), zone * (1 + cfg.drop_zone_tolerance)
    return {"spike_high": spike_high, "zone": zone, "zone_low": lo, "zone_high": hi}


def _post_peak_drawdown(df: pd.DataFrame, cfg: StrategyConfig) -> tuple[float, float, float]:
    """هبوط حقيقي: من القمة إلى أدنى قاع **بعد** تلك القمة فقط."""
    win = df.tail(cfg.peak_lookback)
    lows = win["low"].to_numpy(dtype=float)
    pk, peak = _peak(df, cfg)
    post_low = float(lows[pk:].min())
    dd = 1.0 - post_low / peak if peak > 0 else 0.0
    return peak, post_low, dd


def _targets(entry: float, low2: float, cfg: StrategyConfig) -> dict:
    stop = low2 * (1 - cfg.stop_buffer)
    risk = entry - stop
    # المخاطرة يجب أن تكون موجبة وبمسافة معقولة، وإلا يتضخم حجم المركز بلا حد
    valid = risk > 0 and risk >= entry * cfg.min_risk_pct
    shares = int(cfg.risk_per_trade / risk) if valid else 0
    t = [round(entry * (1 + cfg.target_step * (i + 1)), 4) for i in range(cfg.n_targets)]
    return {"valid": bool(valid), "entry": round(entry, 4), "stop": round(stop, 4), "targets": t,
            "position_size": shares, "risk_per_share": round(risk, 4)}


# ----------------------------------------------------------------- main
def evaluate_symbol(df: pd.DataFrame, short_interest: Optional[float] = None,
                    has_news: Optional[bool] = None,
                    cfg: Optional[StrategyConfig] = None) -> dict:
    cfg = cfg or StrategyConfig()
    w = cfg.weights
    earned: dict[str, float] = {k: 0.0 for k in w}
    # شكل النتيجة ثابت في كل المسارات (حتى عند الاستبعاد المبكر)
    res = {"status": "EXCLUDED", "score": 0.0, "passed": [], "failed": [], "unknown": [],
           "price": None, "breakdown": [], "pattern": {"found": False, "type": None},
           "drop_zone": None, "confirmation": {"confirmed": False}, "trade_plan": None,
           "criteria": {}}
    # حالة كل معيار (صحيح/خطأ) لعرضها في الواجهة
    crit = {"price": False, "short": False, "ma": False, "negative": False,
            "drop_zone": False, "pattern": False, "confirm": False,
            "resistance": False, "news": False}
    res["criteria"] = crit

    def award(key: str, pts: float) -> None:
        earned[key] += pts

    # ---- فلاتر صلبة (بيانات/نوع السهم فقط) ----
    if df is None or len(df) < 60:
        res["failed"].append("بيانات غير كافية (أقل من 60 شمعة)")
        return res
    df = df.dropna(subset=["open", "high", "low", "close"]).copy()
    if len(df) < 60:
        res["failed"].append("بيانات غير كافية بعد إزالة القيم الفارغة (أقل من 60 شمعة)")
        return res
    price = float(df["close"].iloc[-1])
    res["price"] = price
    if not (cfg.price_hard_min <= price <= cfg.price_hard_max):
        res["failed"].append(f"السعر {price:.2f} خارج نطاق البني ستوك ({cfg.price_hard_min}-{cfg.price_hard_max})")
        return res

    # ---- السعر 1.5–2 ----
    if cfg.price_ideal_min <= price <= cfg.price_ideal_max:
        award("price", w["price"]); crit["price"] = True; res["passed"].append("السعر ضمن 1.50–2.00")
    elif price < 5:
        award("price", w["price"] * 0.5); res["passed"].append("السعر بني ستوك لكن خارج 1.5–2 (نقاط جزئية)")
    else:
        res["failed"].append("السعر أعلى من 5 (لا نقاط)")

    # ---- الشورت ----
    if short_interest is None or (isinstance(short_interest, float) and np.isnan(short_interest)):
        award("short", w["short"] * 0.5); res["unknown"].append("الشورت غير متوفر (محايد)")
    elif short_interest < cfg.short_interest_max:
        award("short", w["short"]); crit["short"] = True; res["passed"].append(f"الشورت {int(short_interest):,} < {cfg.short_interest_max:,}")
    else:
        res["failed"].append(f"الشورت {int(short_interest):,} ≥ {cfg.short_interest_max:,}")

    # ---- المتوسطات 20/30/50 ----
    for p in cfg.ma_periods:
        df[f"sma{p}"] = df["close"].rolling(p).mean()
    s20, s30, s50 = (df[f"sma{p}"].iloc[-1] for p in cfg.ma_periods)
    ma_pts = 0.0
    if price >= s20: ma_pts += 0.5
    if df["sma20"].iloc[-1] > df["sma20"].iloc[-3]: ma_pts += 0.25   # ميل MA20 للأعلى
    if price >= s30: ma_pts += 0.25
    award("ma", w["ma"] * ma_pts)
    crit["ma"] = ma_pts >= 0.5
    (res["passed"] if ma_pts >= 0.5 else res["failed"]).append(
        f"المتوسطات: السعر {'فوق' if price >= s20 else 'تحت'} MA20 "
        f"(MA20={s20:.2f}, MA30={s30:.2f}, MA50={s50:.2f})")

    # ---- السلوك السلبي السابق (من القمة إلى ما بعدها) ----
    peak, post_low, dd_post = _post_peak_drawdown(df, cfg)
    if dd_post >= cfg.min_drawdown:
        award("negative", w["negative"]); crit["negative"] = True
        res["passed"].append(f"سلوك سلبي سابق: هبوط {dd_post:.0%} من القمة ({peak:.2f}) إلى {post_low:.2f}")
    else:
        res["failed"].append(f"لا هبوط كافٍ بعد القمة ({dd_post:.0%})")

    # ---- النمط (قبل منطقة الهبوط لأنها تعتمد على القاع الثاني) ----
    pat = _find_pattern(df, cfg)
    if pat["found"] and price < pat["low2"] * (1 - cfg.stop_buffer):
        # السعر كسر وقف النمط: النمط منكسر ولا يُحسب
        pat = {"found": False, "type": None, "broken": True, "low2": pat["low2"], "low1": pat["low1"]}
        res["failed"].append("النمط منكسر: السعر تحت وقف القاع الثاني")
    res["pattern"] = pat

    # ---- منطقة الهبوط: القمة ÷ 2 ----
    dz = _drop_zone(df, cfg)
    res["drop_zone"] = dz
    ref = pat["low2"] if pat["found"] else price   # قرب القاع الثاني (إن وجد) أو السعر الحالي
    if dz["zone_low"] <= ref <= dz["zone_high"]:
        award("drop_zone", w["drop_zone"]); crit["drop_zone"] = True
        res["passed"].append(f"القاع/السعر {ref:.2f} داخل منطقة القمة÷2 ({dz['zone']:.2f})")
    elif dz["zone_low"] * 0.8 <= ref <= dz["zone_high"] * 1.2:
        award("drop_zone", w["drop_zone"] * 0.5)
        res["passed"].append(f"قريب من منطقة القمة÷2 ({dz['zone']:.2f}) (جزئي)")
    else:
        res["failed"].append(f"بعيد عن منطقة القمة÷2 ({dz['zone']:.2f})")

    # ---- النمط + التأكيد ----
    conf = _confirmation(df, pat) if pat["found"] else {"confirmed": False}
    res["confirmation"] = conf
    if pat["found"]:
        award("pattern", w["pattern"]); crit["pattern"] = True
        label = "سحب سيولة" if pat["type"] == "liquidity_sweep" else "قاع ثانٍ (دعم ثابت)"
        res["passed"].append(f"نمط: {label} — قاع1={pat['low1']:.2f}, قاع2={pat['low2']:.2f}")
    else:
        res["failed"].append("لم يظهر قاع ثانٍ/سحب سيولة بعد")
    if conf["confirmed"]:
        award("confirm", w["confirm"]); crit["confirm"] = True
        res["passed"].append("إغلاق فوق القاع + شمعة تأكيد تكسر القمة السابقة")
    elif pat["found"]:
        res["failed"].append("بانتظار شمعة التأكيد (إغلاق أخضر يكسر قمة الشمعة السابقة)")

    # ---- مجال المقاومة 20%+ ----
    res_level = df["high"].tail(cfg.pattern_lookback).max()
    room = res_level / price - 1
    if room >= cfg.min_resistance_room:
        award("resistance", w["resistance"]); crit["resistance"] = True
        res["passed"].append(f"مجال صعود حتى المقاومة {room:.0%}")
    else:
        res["failed"].append(f"المقاومة قريبة ({room:.0%} فقط)")

    # ---- الأخبار ----
    if has_news is None:
        award("news", w["news"] * 0.5); res["unknown"].append("الأخبار غير متوفرة (محايد)")
    elif has_news:
        award("news", w["news"]); crit["news"] = True; res["passed"].append("يوجد خبر/إعلان")
    else:
        res["failed"].append("لا يوجد خبر")

    # ---- الدرجة الكلية وتفصيلها ----
    score = float(sum(earned.values()))
    res["score"] = round(score, 1)
    res["breakdown"] = [
        {"key": k, "ar": WEIGHT_LABELS_AR.get(k, k), "weight": float(w[k]),
         "points": round(earned[k], 2), "score": round(earned[k] / w[k] * 100, 1) if w[k] else 0.0}
        for k in w
    ]

    # ---- الحالة النهائية ----
    if score >= cfg.ready_score and conf["confirmed"]:
        res["status"] = "READY"          # جاهز للدخول
    elif score >= cfg.near_score and pat["found"]:
        res["status"] = "NEAR"           # قريب من الاكتمال
    elif score >= cfg.watch_score:
        res["status"] = "WATCH"          # مراقبة
    else:
        res["status"] = "EXCLUDED"
        res["failed"].append(f"الدرجة {score:.0f} أقل من حد المراقبة {cfg.watch_score}")

    # ---- خطة الصفقة (فقط عند وجود نمط غير منكسر) ----
    if pat["found"]:
        entry = conf["entry"] if conf.get("entry") else price
        plan = _targets(entry, pat["low2"], cfg)
        res["trade_plan"] = plan
        if not plan["valid"]:
            res["failed"].append("خطة الصفقة غير صالحة: مخاطرة السهم صفر أو سالبة أو أقل من الحد الأدنى")
    return res


STATUS_AR = {"READY": "جاهز للدخول", "NEAR": "قريب من الاكتمال",
             "WATCH": "مراقبة", "EXCLUDED": "مستبعد"}
