"""إشارات فنية اختيارية فقط؛ غياب أي إشارة يمنح صفراً ولا يرفض السهم."""
from __future__ import annotations

import math
import pandas as pd


def technical_components(df: pd.DataFrame, patterns: list[dict], params, start=None) -> list[tuple]:
    """أوزان ثابتة المقام؛ النماذج بدائل OR ولا تُشترط معاً.

    الدعم لكل شمعة هو أدنى سعر في الجلسات العشرين السابقة (دون الشمعة نفسها).
    تُرصد الاختبارات والسحوبات في آخر خمس جلسات فقط، بلا معلومات مستقبلية.
    """
    def finite(x):
        return x is not None and math.isfinite(float(x))

    ema_df = df
    if start is not None:
        df = df.loc[pd.Timestamp(start):]
    recent = df.tail(3)["volume"].mean()
    prior = df.iloc[-23:-3]["volume"].mean()
    ratio = float(recent / prior) if len(df) >= 23 and finite(prior) and prior > 0 and finite(recent) else None
    dry = 0.0
    if ratio is not None and 0 <= ratio < params.max_volume_dryup_ratio:
        dry = min(100.0, (1 - ratio / params.max_volume_dryup_ratio) * 200)

    names = {"double_bottom": "قاع مزدوج W", "falling_wedge": "وتد هابط",
             "rsi_divergence": "تباعد RSI صاعد"}
    detected = []
    for p in patterns:
        if p.get("name") in names and p.get("bias") == "bullish" and p.get("status") != "invalidated":
            confidence = p.get("confidence")
            if finite(confidence):
                detected.append((names[p["name"]], max(0, min(1, confidence)) * 100))
    # أفضل نموذج يكفي لكامل حصة النماذج بحسب درجة ثقته؛ لا AND بين النماذج.
    pattern_score = max((v for _, v in detected), default=0.0)

    tests = sweeps = False
    for i in range(max(20, len(df) - 5), len(df)):
        bar = df.iloc[i]
        support = df["low"].iloc[max(0, i - 20):i].min()
        if not finite(support) or support <= 0:
            continue
        low, close = float(bar["low"]), float(bar["close"])
        tests |= bool(support <= low <= support * (1 + params.support_retest_tolerance_pct / 100)
                      and close >= support)
        sweeps |= bool(low <= support * (1 - params.sweep_tolerance_pct / 100)
                       and close >= support * (1 + params.reclaim_buffer_pct / 100))
    support_score = (40 if tests else 0) + (60 if sweeps else 0)

    df = ema_df  # المتوسطات على التاريخ المعدّل بالكامل، مع الحفاظ على فترة التهيئة
    ma_scores, ma_details = [], []
    for period in (20, 30, 50):
        col = f"ema{period}"
        if col not in df or len(df) < period or not finite(df[col].iloc[-1]):
            ma_scores.append(0)
            continue
        close, avg = float(df["close"].iloc[-1]), float(df[col].iloc[-1])
        above = close >= avg
        rising = len(df) >= 4 and avg > float(df[col].iloc[-4])
        ma_scores.append((70 if above else 0) + (30 if rising else 0))
        ma_details.append(f"EMA{period}: {'فوقه' if above else 'تحته'} / {'صاعد' if rising else 'غير صاعد'}")

    return [
        ("volume", "جفاف الحجم", dry, params.weights["volume"],
         f"متوسط 3 جلسات / العشرين السابقة: {ratio:.2f}" if ratio is not None else "بيانات الحجم غير كافية — صفر نقاط"),
        ("patterns", "النماذج الفنية البديلة", pattern_score, params.weights["patterns"],
         " · ".join(name for name, _ in detected) or "لا نموذج مؤكد/متكوّن — صفر نقاط؛ لا استبعاد"),
        ("support", "اختبار الدعم وسحب السيولة", support_score, params.weights["support"],
         f"اختبار دعم: {'نعم' if tests else 'لا'} · سحب واستعادة: {'نعم' if sweeps else 'لا'}"),
        ("ema", "تفاعل EMA 20 / 30 / 50", sum(ma_scores) / 3, params.weights["ema"],
         " · ".join(ma_details) or "بيانات المتوسطات غير كافية — صفر نقاط"),
    ]
