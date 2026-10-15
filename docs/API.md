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
  "symbol": "JZ", "name": "JZ Holdings Inc.", "data_source": "demo", "as_of": "2026-10-13",
  "rs_found": true, "rs_date": "2026-09-10", "rs_age_days": 34, "rs_ratio_str": "1-for-8", "rs_ratio": 8.0,
  "rs_open": 3.0, "spike_high": 3.4, "spike_pct": 13.33,
  "decline_pct": -52.94, "bottom_price": 1.6, "bottom_date": "2026-10-05",
  "base_sessions": 6, "base_range_pct": 9.2, "volume_dryup": 0.75, "base_broken": false,
  "rsi_trough": 14.89, "rsi_now": 28.49, "rsi_recovered": true, "rsi_sessions_below_30": 19,
  "above_ema9_sessions": 1, "macd_turning": true, "channel_broken": false, "rvol": 1.65, "atr_pct": 6.75,
  "shares_out": 9500000, "float_shares": 3200000, "short_pct_float": 18.4, "days_to_cover": 3.1,
  "phase": 6, "watchlist": true, "eligible": true,
  "gam_candidate": false, "gam_reasons": [], "gam_rsi_scope": "current_scan_price_series",
  "rejection_codes": [], "score": 82.1, "grade": "A",
  "levels": { "trigger": 1.7678, "stop": 1.6007, "stop_structural": 1.564,
              "targets": { "t1_382": 2.2876, "t2_618": 2.7123, "t3_full": 3.3999 }, "rr_t2": 5.65 },
  "breakdown": [ { "key": "base", "ar": "القاع والثبات", "weight": 18, "score": 100, "points": 18.0 } ],
  "patterns_daily": [ { "name_ar": "جفاف حجم ثم انفجار", "confidence": 0.9, "status": "confirmed" } ],
  "catalysts": [ { "date": "2026-11-12", "type": "earnings", "sentiment": "neutral", "title": "..." } ],
  "dilution": [ { "date": "2026-09-28", "kind": "S-1", "severity": "medium", "title": "..." } ],
  "why_ar": [ "..." ], "action_ar": "جاهز فنياً: الدخول بعد اختراق 1.7678 ..."
}
```

## حقول وسياسة GAM

- `summary.gam_candidates`: عدد المرشحين في المسار الثانوي، منفصل عن `eligible` و`watchlist`.
- `gam_candidate=true` لا يظهر إلا مع تقسيم عكسي حديث وسعر 0.50–20$، وحين تكون كل
  `rejection_codes` محصورة في `decline` و/أو `rsi`.
- `gam_reasons`: نصوص أسباب الرفض نفسها، وليست أسباب ترقية.
- `gam_rsi_scope=current_scan_price_series` و`/api/strategy.gam_policy.historical_state_persisted=false`:
  قراءة RSI تخص بيانات المسح الحالي ولا توجد حالة رفض محفوظة بين المسوح.
- مرشح GAM يبقى دائمًا `eligible=false` و`watchlist=false`؛ وهو ليس توصية.

## كودات الحالة

- `200` نجاح · `400` طلب غير صالح (مثلًا لا توجد رموز لتوليد التقارير) ·
  `404` ملف تقرير غير موجود · `500` خطأ في التحليل (يظهر تفصيله في الاستجابة).
