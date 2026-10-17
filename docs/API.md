# واجهة برمجة التطبيقات — API Reference

المخدم: FastAPI — التشغيل: `python -m app.server` (المنفذ الافتراضي 8000)

| الطريقة | المسار | الوصف |
|---------|--------|-------|
| GET | `/` | لوحة الداشبورد (HTML) |
| GET | `/api/health` | الحالة + المزوّدون + حالة المسح |
| GET | `/api/strategy` | معاملات الاستراتيجية والمراحل ومفاتيح التقييم |
| GET | `/api/universe?limit=60&mode=demo` | قائمة الرموز المرصودة |
| POST | `/api/scan/start?limit=60&mode=demo&symbols=JZ,NVEX` | بدء مسح في الخلفية |
| GET | `/api/scan/status` | نسبة التقدّم (`percent`, `progress`, `total`) |
| GET | `/api/scan/results?only_watch=false&include_rejected=true` | نتائج المسح + الملخص |
| GET | `/api/analysis/{symbol}?with_series=true` | التحليل الكامل لرمز (بما فيه تصنيف GAM والسلاسل الزمنية) |
| GET | `/api/chart/{symbol}?tf=1D\|4H` | حِزمة الرسم (شموع/متوسطات/مؤشرات/أحداث/مستويات) |
| GET | `/api/report/{symbol}?fmt=html\|md\|json` | تقرير تحليلي كامل |
| POST | `/api/report/save?symbols=JZ,NVEX` | توليد وكتابة التقارير لكل الرموز في `reports/output` |
| GET | `/api/demo-metadata` | وصف السيناريوهات التجريبية |
| GET | `/reports/{filename}` | تنزيل تقرير مولّد (html/md) |

## قاعدة الأهلية (خمسة فلاتر فقط)

`eligible=true` يعني نجاح كل `core_filters` الخمسة واكتمال قراءتها: السعر 1–5$ شاملًا
الحدين، تقسيم عكسي منفّذ خلال ≤60 يوماً بعامل >1، هبوط ≥30% من قمة ما بعد التقسيم،
RSI<30 أثناء تكوّن القاع، وShort Shares<20,000 حصرياً. النماذج والثبات وEMA ودورة
الحياة تُرجع في `breakdown`/`patterns_daily`/`lifecycle_checks` ولا تستبعد أحداً.
`/api/scan/results` يُرجع كل النتائج افتراضياً للتشخيص؛ استخدم `include_rejected=false`
للجدول الرئيسي (المؤهلين فقط مرتبين بالنقاط). كل صف يحمل `core_filters` الخمسة مع
`value` و`display` و`reason` لتدقيق القرار.

## أمثلة

```bash
# 1) ابدأ المسح ثم راقب التقدّم
curl -X POST "http://localhost:8000/api/scan/start?limit=60"
curl "http://localhost:8000/api/scan/status"

# 2) جدول الفرص
curl "http://localhost:8000/api/scan/results?only_watch=true" | python -m json.tool

# 3) تحليل سهم واحد (المستويات + النماذج + الأخبار)
curl "http://localhost:8000/api/analysis/JZ" | python -m json.tool

# 4) حِزمة الرسم للفريم اليومي أو 4 ساعات
curl "http://localhost:8000/api/chart/JZ?tf=4H"

# 5) تقرير Markdown أو HTML
curl "http://localhost:8000/api/report/JZ?fmt=md"
curl -X POST "http://localhost:8000/api/report/save"       # يكتب كل التقارير + تقرير القائمة
```

## بنية استجابة التحليل (مختصر)

```jsonc
{
  "symbol": "JZ", "name": "JZ Holdings Inc.", "data_source": "demo", "as_of": "2026-10-16",
  "rs_found": true, "rs_date": "2026-09-15", "rs_age_days": 32, "rs_ratio_str": "1-for-8", "rs_ratio": 8.0,
  "rs_open": 3.0, "spike_high": 3.3999, "spike_pct": 13.33,
  "decline_pct": -55.06, "bottom_price": 1.6, "bottom_date": "2026-09-25",
  "base_sessions": 5, "base_range_pct": 10.9, "volume_dryup": 0.44, "base_broken": false,
  "rsi_trough": 11.82, "rsi_now": 31.02, "rsi_recovered": true, "rsi_sessions_below_30": 11,
  "above_ema9_sessions": 0, "macd_turning": true, "channel_broken": false, "rvol": 1.16, "atr_pct": 7.22,
  "shares_out": 9500000, "float_shares": 3200000, "short_shares": 16000, "short_pct_float": 0.5, "days_to_cover": 3.1,
  "phase": 6, "watchlist": true, "eligible": true,
  "gam_candidate": false, "gam_reasons": [], "gam_rsi_scope": "current_scan_price_series",
  "rejection_codes": [], "score": 64.5, "grade": "C",
  "core_filters": [
    { "key": "price", "label_ar": "السعر ضمن نطاق البيني", "rule_ar": "1$ ≤ آخر إغلاق ≤ 5$ (الحدان مشمولان)",
      "value": 1.681, "display": "1.6810$", "ok": true, "data_available": true, "reason": "" },
    { "key": "reverse_split", "label_ar": "تقسيم عكسي منفّذ حديثاً", "rule_ar": "خلال ≤ 60 يوماً وبعامل > 1",
      "value": 32, "display": "1-for-8 · 2026-09-15 · عمر 32 يوم", "ok": true, "data_available": true, "reason": "" },
    { "key": "decline", "label_ar": "هبوط كافٍ من قمة ما بعد التقسيم", "rule_ar": "هبوط ≥ 30%",
      "value": -55.0575, "display": "55.06% من قمة ما بعد التقسيم", "ok": true, "data_available": true, "reason": "" },
    { "key": "rsi", "label_ar": "تشبع بيعي أثناء تكوّن القاع", "rule_ar": "RSI(14) < 30",
      "value": 11.82, "display": "أدنى RSI 11.82", "ok": true, "data_available": true, "reason": "" },
    { "key": "short_shares", "label_ar": "انخفاض الأسهم المكشوفة", "rule_ar": "عدد الأسهم المكشوفة < 20,000 سهم",
      "value": 16000, "display": "16,000 سهم", "ok": true, "data_available": true, "reason": "" }
  ],
  "levels": { "trigger": 2.0355, "stop": 1.8298, "stop_structural": 1.4912,
              "targets": { "t1_382": 2.2876, "t2_618": 2.7123, "t3_full": 3.3999 }, "rr_t2": 3.29 },
  "breakdown": [
    { "key": "volume", "ar": "جفاف الحجم", "weight": 25, "score": 100, "points": 25.0 },
    { "key": "patterns", "ar": "النماذج الفنية البديلة", "weight": 35, "score": 70, "points": 24.5 },
    { "key": "support", "ar": "اختبار الدعم وسحب السيولة", "weight": 25, "score": 60, "points": 15.0 },
    { "key": "ema", "ar": "تفاعل EMA 20 / 30 / 50", "weight": 15, "score": 0, "points": 0.0 }
  ],
  "patterns_daily": [ { "name_ar": "ديفرجنس إيجابي RSI", "confidence": 0.7, "status": "confirmed" } ],
  "catalysts": [ { "date": "2026-11-12", "type": "earnings", "sentiment": "neutral", "title": "..." } ],
  "dilution": [ { "date": "2026-09-28", "kind": "S-1", "severity": "medium", "title": "..." } ],
  "why_ar": [ "..." ], "action_ar": "جاهز فنياً: الدخول بعد اختراق 2.0355 ..."
}
```

## حقول وسياسة GAM

- `summary.gam_candidates`: عدد المرشحين في المسار الثانوي، منفصل عن `eligible` و`watchlist`.
- `gam_candidate=true` لا يظهر إلا مع تقسيم عكسي حديث وسعر داخل نطاق الفلاتر 1–5$، وحين تكون كل
  `rejection_codes` محصورة في `decline` و/أو `rsi`.
- `gam_reasons`: نصوص أسباب الرفض نفسها، وليست أسباب ترقية.
- `gam_rsi_scope=current_scan_price_series` و`/api/strategy.gam_policy.historical_state_persisted=false`:
  قراءة RSI تخص بيانات المسح الحالي ولا توجد حالة رفض محفوظة بين المسوح.
- مرشح GAM يبقى دائمًا `eligible=false` و`watchlist=false`؛ وهو ليس توصية.

## كودات الحالة

- `200` نجاح · `400` طلب غير صالح (مثلًا لا توجد رموز لتوليد التقارير) ·
  `404` ملف تقرير غير موجود · `500` خطأ في التحليل (يظهر تفصيله في الاستجابة).
