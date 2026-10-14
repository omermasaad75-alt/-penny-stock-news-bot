"""مولّد التقارير — تقرير تحليلي كامل (Markdown + HTML) لكل سهم أو لقائمة الفرص."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import REPORTS_DIR, STRATEGY, settings
from .providers.hub import DataHub
from .strategy import engine as eng


def _n(x, nd=2, dash="—"):
    if x is None:
        return dash
    try:
        return f"{float(x):,.{nd}f}"
    except (TypeError, ValueError):
        return str(x)


def _pct(x, nd=1, dash="—"):
    if x is None:
        return dash
    try:
        return f"{float(x):+,.{nd}f}%"
    except (TypeError, ValueError):
        return str(x)


def _m(x, dash="—"):
    if x is None:
        return dash
    try:
        v = float(x)
    except (TypeError, ValueError):
        return dash
    if v >= 1e9:
        return f"{v/1e9:.2f}B"
    if v >= 1e6:
        return f"{v/1e6:.2f}M"
    if v >= 1e3:
        return f"{v/1e3:.1f}K"
    return f"{v:.0f}"


def analysis_to_markdown(a: eng.Analysis, sources: dict | None = None) -> str:
    p = STRATEGY
    info = eng.phase_info(a.phase if a.eligible else (a.phase if a.rs_found else 0))
    lv = a.levels or {}
    tgt = lv.get("targets", {})
    lines: list[str] = []
    add = lines.append

    add(f"# تقرير تحليلي — {a.symbol} ({a.name})")
    add("")
    add(f"> **المرحلة:** {info['ar']}  |  **التقييم:** {a.score}/100 ({a.grade})  |  "
        f"**مصدر البيانات:** {a.data_source}  |  **آخر جلسة:** {a.as_of}")
    add("")
    if a.note_ar:
        add(f"**وصف الحالة:** {a.note_ar}")
        add("")

    # ---------------- الملخص التنفيذي ---------------- #
    add("## 1) الملخص التنفيذي")
    add("")
    for w in a.why_ar:
        add(f"- {w}")
    add("")
    add(f"**خطة العمل:** {a.action_ar}")
    add("")

    # ---------------- شروط الاستراتيجية ---------------- #
    add("## 2) مطابقة شروط الاستراتيجية")
    add("")
    add("| # | الشرط | المعيار | القراءة الفعلية | الحالة |")
    add("|---|-------|---------|----------------|--------|")
    checks = [
        ("1", "تقسيم عكسي 1:2 – 1:60 خلال شهرين", f"≤ {p.rs_lookback_days} يوماً",
         f"{a.rs_ratio_str or '—'} بتاريخ {a.rs_date or '—'} ({a.rs_age_days if a.rs_age_days is not None else '—'} يوم)",
         bool(a.rs_found)),
        ("2", "صعود يوم التقسيم", f"≥ {p.min_spike_pct:.0f}%",
         _pct(a.spike_pct), bool(a.spike_pct is not None and a.spike_pct >= p.min_spike_pct)),
        ("3", "موجة هبوط قوية", f"≥ {p.min_drop_pct:.0f}% (المثالي {p.good_drop_pct:.0f}%+)",
         _pct(a.decline_pct), bool(a.decline_pct is not None and abs(a.decline_pct) >= p.min_drop_pct)),
        ("4", "تشبع بيعي RSI", f"< {p.rsi_oversold:.0f}",
         f"القاع {_n(a.rsi_trough,1)} / الحالي {_n(a.rsi_now,1)}"
         + (" (بدأ التحسّن)" if a.rsi_recovered else ""),
         bool(a.rsi_trough is not None and a.rsi_trough < p.rsi_oversold)),
        ("5", "ثبات على القاع", f"≥ {p.min_base_sessions} جلسات",
         f"{eng.sessions_ar(a.base_sessions)} على {_n(a.bottom_price,3)}"
         + (f" ونطاق {_n(a.base_range_pct,1)}%" if a.base_range_pct is not None else ""),
         bool((a.base_sessions or 0) >= p.min_base_sessions)),
        ("6", "جفاف الحجم داخل القاعدة", f"< {p.max_volume_dryup_ratio*100:.0f}% من المتوسط",
         f"{_n((a.volume_dryup or 0)*100,0)}%" if a.volume_dryup else "—",
         bool(a.volume_dryup is not None and a.volume_dryup < p.max_volume_dryup_ratio)),
        ("7", "تأكيد الانعكاس", f"≥ 3 تأكيدات فنية", f"{len(a.confirmations)} تأكيد",
         len(a.confirmations) >= 3),
    ]
    for c in checks:
        add(f"| {c[0]} | {c[1]} | {c[2]} | {c[3]} | {'✅ محقق' if c[4] else '⏳ غير مكتمل'} |")
    add("")
    if a.rejections:
        add("**أسباب الاستبعاد:**")
        add("")
        for r in a.rejections:
            add(f"- ❌ {r}")
        add("")
    if a.notes:
        for nt in a.notes:
            add(f"- ⓘ {nt}")
        add("")

    # ---------------- دورة الحياة بعد الثبات (شرط «جاهز فنياً») ---------------- #
    add("## 2.ب) دورة الحياة بعد الثبات — شرط «جاهز فنياً»")
    add("")
    add(f"**حالة التسلسل:** {a.lifecycle_stage or '—'} "
        + ("✅ **جاهز فنياً**" if a.lifecycle_ready else
           ("⛔ فشل التسلسل" if a.lifecycle_failed else "⏳ قيد التكوّن")))
    add("")
    add("| # | الخطوة | المعيار | القراءة الفعلية | الحالة |")
    add("|---|--------|---------|-----------------|--------|")
    for i, c in enumerate(a.lifecycle_checks or [], 1):
        mark = {"ok": "✅ محقق", "active": "⏳ جارٍ الآن", "pending": "• لم يبدأ"}.get(c.get("state"), "—")
        add(f"| {i} | {c['ar']} | {c['std']} | {c['detail']} | {mark} |")
    add("")
    if a.lifecycle_failed:
        add("> ⛔ **إلغاء السيناريو:** كسر السعر الدعم الرئيسي بعد الانطلاقة دون استعادة — "
            "لا دخول حتى يتكوّن قاع جديد.")
        add("")
    if a.pullback_type == "liquidity_sweep":
        add(f"- **سحب سيولة (Liquidity Sweep):** الفتيل كسر الدعم إلى {_n(a.sweep_low,3)}$ "
            f"({_n(a.sweep_depth_pct,1)}% تحت الدعم) بتاريخ {a.sweep_date or '—'} ثم أُغلق فوقه.")
        add(f"- **وقف إلغاء السيناريو انتقل إلى** {_n((a.levels or {}).get('stop_structural'),3)}$ "
            f"(تحت قاع السحب {_n(a.sweep_low,3)}$).")
        add("")
    add(f"- **المستويات المرجعية:** الدعم الرئيسي {_n(a.support_level or a.bottom_price,3)}$ — "
        f"أقرب مقاومة {_n(a.resistance_level,3)}$ — قمة الانطلاقة {_n(a.impulse_high,3)}$.")
    add("")

    # ---------------- التفاصيل الفنية ---------------- #
    add("## 3) التفاصيل الفنية")
    add("")
    add("| البند | القيمة |")
    add("|-------|--------|")
    add(f"| سعر الافتتاح يوم التقسيم | {_n(a.rs_open,3)}$ |")
    add(f"| قمة ما بعد التقسيم | {_n(a.spike_high,3)}$ ({a.spike_date or '—'}) |")
    add(f"| القاع المرجعي | {_n(a.bottom_price,3)}$ ({a.bottom_date or '—'}) |")
    add(f"| نطاق القاعدة | {_n(a.base_range_pct,1)}% |")
    add(f"| الانطلاقة بعد الثبات | {_pct(a.impulse_pct)} إلى {_n(a.impulse_high,3)}$ ({a.impulse_date or '—'}) |")
    add(f"| المقاومة القريبة | {_n(a.resistance_level,3)}$ — تشكّلت {a.resistance_date or '—'}"
        + (f"، واختُبرت في {a.impulse_date}" if a.impulse_date else "")
        + (f" (على {_n(a.resistance_distance_pct,1)}% من الدعم) |" if a.resistance_distance_pct is not None else " |"))
    if a.pullback_type == "liquidity_sweep":
        add(f"| سحب السيولة | {_n(a.sweep_low,3)}$ ({_n(a.sweep_depth_pct,1)}% تحت الدعم) في {a.sweep_date or '—'} |")
    elif a.pullback_low is not None:
        add(f"| العودة للدعم | أدنى نقطة {_n(a.pullback_low,3)}$ ({a.pullback_date or '—'}) — "
            f"{'إعادة اختبار ناجحة' if a.pullback_type == 'support_retest' else 'تحت المراقبة'} |")
    add(f"| الاستعادة فوق الدعم | {a.reclaim_sessions} إغلاق فوق {_n(a.reclaim_level,3)}$ "
        f"(آخرها {a.reclaim_date or '—'}) |")
    add(f"| الإقفال الحالي | {_n(a.last_close,3)}$ ({_pct(a.change_pct,2)}) |")
    add(f"| RSI (14) اليومي | {_n(a.rsi_now,1)} (أدنى {_n(a.rsi_trough,1)} بتاريخ {a.rsi_trough_date or '—'}) |")
    add(f"| MACD Histogram | {_n(a.macd_hist,4)} — {'يتحسّن' if a.macd_turning else 'لم يتحسّن بعد'} |")
    add(f"| الحجم النسبي (RVOL) | {_n(a.rvol,2)}x |")
    add(f"| ATR% | {_n(a.atr_pct,2)}% |")
    add(f"| القناة الهابطة | انحدار {_n(a.channel_slope_pct,2)}% / سقف {_n(a.channel_upper,3)}$ — "
        f"{'مخترقة ✅' if a.channel_broken else 'غير مخترقة'} |")
    add(f"| بنية القيعان | {a.higher_lows} قاع صاعد منذ القاع |")
    add("")
    if a.h4.get("available"):
        add("### فريم 4 ساعات")
        add("")
        add(f"- الاتجاه: { {'up':'صاعد','down':'هابط','neutral':'عرضي'}.get(a.h4.get('trend'),'—') }"
            f" | RSI: {_n(a.h4.get('rsi'),1)} | MACD Hist: {_n(a.h4.get('macd_hist'),4)}"
            f" | RVOL: {_n(a.h4.get('rvol'),2)}x")
        add(f"- توافق مع الفريم اليومي: {'نعم ✅' if a.h4.get('aligned') else 'لا ⏳'}"
            f" | قناة مخترقة: {'نعم' if a.h4.get('channel_broken') else 'لا'}"
            f" | ديفرجنس إيجابي: {'نعم' if a.h4.get('bullish_divergence') else 'لا'}")
        add("")

    # ---------------- النماذج الفنية ---------------- #
    add("## 4) النماذج الفنية المرصودة")
    add("")
    add("| النموذج | الفريم | الثقة | الحالة | الوصف |")
    add("|---------|-------|-------|--------|-------|")
    for pt in (a.patterns_daily + a.patterns_4h):
        add(f"| {pt['name_ar']} | {pt['tf']} | {int(pt['confidence']*100)}% | "
            f"{'مؤكد' if pt['status']=='confirmed' else 'قيد التشكّل'} | {pt['description_ar']} |")
    add("")

    # ---------------- المستويات ---------------- #
    add("## 5) خطة التداول والمستويات")
    add("")
    add("| البند | المستوى |")
    add("|-------|---------|")
    add(f"| نقطة الاختراق (Trigger) | **{_n(lv.get('trigger'),3)}$** |")
    add(f"| منطقة الدخول | {_n((lv.get('entry_zone') or [None])[0],3)}$ – {_n(lv.get('trigger'),3)}$ |")
    add(f"| وقف الخسارة التداولي | {_n(lv.get('stop'),3)}$ ({_pct(lv.get('stop_pct'),1)}) |")
    add(f"| وقف إلغاء السيناريو (بنيوي) | {_n(lv.get('stop_structural'),3)}$ |")
    add(f"| الهدف 1 (38.2% ارتداد) | {_n(tgt.get('t1_382'),3)}$ |")
    add(f"| الهدف 2 (61.8% ارتداد) | {_n(tgt.get('t2_618'),3)}$ |")
    add(f"| الهدف 3 (القمة السابقة) | {_n(tgt.get('t3_full'),3)}$ |")
    add(f"| مخاطرة/عائد عند الهدف 1 | {_n(lv.get('rr_t1'),2)} : 1 |")
    add(f"| مخاطرة/عائد عند الهدف 2 | {_n(lv.get('rr_t2'),2)} : 1 |")
    add("")
    add(f"> {lv.get('position_note_ar','')}")
    add("")

    # ---------------- الأساسيات ---------------- #
    add("## 6) البيانات الأساسية والأساس المالي")
    add("")
    add("| البند | القيمة |")
    add("|-------|--------|")
    add(f"| اسم الشركة | {a.name} |")
    add(f"| البورصة / القطاع | {a.exchange or '—'} / {a.sector or '—'} |")
    add(f"| عدد الأسهم القائمة | {_m(a.shares_out)} |")
    add(f"| الفلوت (الأسهم الحرة) | {_m(a.float_shares)} |")
    add(f"| القيمة السوقية | {_m(a.market_cap)}$ |")
    add(f"| متوسط الحجم اليومي | {_m(a.avg_volume)} |")
    add(f"| متوسط قيمة التداول | {_m(a.dollar_volume)}$ |")
    add(f"| نسبة البيع على المكشوف من الفلوت | {_n(a.short_pct_float,1)}% |")
    add(f"| أسهم مبيعة على المكشوف | {_m(a.short_shares)} |")
    add(f"| أيام التغطية (Days to Cover) | {_n(a.days_to_cover,1)} |")
    add(f"| نسبة حجم البيع المكشوف اليومي | {_n((a.short_volume_ratio or 0)*100,1)}% |")
    add(f"| المصدر | {a.short_source or '—'} |")
    add("")

    # ---------------- الأخبار ---------------- #
    add("## 7) الأخبار والمحفزات (إيجابية/سلبية/مستقبلية)")
    add("")
    if a.catalysts:
        add("| التاريخ | النوع | الاتجاه | الأثر | العنوان |")
        add("|--------|------|---------|-------|---------|")
        for c in a.catalysts[:20]:
            ar = {"positive": "إيجابي 🟢", "negative": "سلبي 🔴", "neutral": "محايد ⚪"}.get(c.get("sentiment"), "—")
            add(f"| {c.get('date')} | {c.get('type')} | {ar} | {c.get('impact')} | {str(c.get('title'))[:110]} |")
    else:
        add("_لا توجد أخبار مرصودة._")
    add("")
    if a.dilution:
        add("### إفصاحات التخفيف (Dilution)")
        add("")
        for d in a.dilution[:10]:
            add(f"- [{d.get('severity')}] {d.get('date')} — {d.get('title')}")
        add("")
    if a.risk_flags:
        add("### تحذيرات المخاطر")
        add("")
        for r in a.risk_flags:
            add(f"- {r}")
        add("")

    # ---------------- التقييم ---------------- #
    add("## 8) تفصيل التقييم (0-100)")
    add("")
    add("| المكوّن | الوزن | الدرجة | النقاط | التفصيل |")
    add("|---------|-------|--------|--------|---------|")
    for b in a.breakdown:
        add(f"| {b['ar']} | {b['weight']} | {b['score']} | {b['points']} | {b['detail']} |")
    add(f"| **المجموع** | **100** | | **{a.score}** | التقدير {a.grade} |")
    add("")

    # ---------------- التذييل ---------------- #
    add("---")
    add("")
    add(f"_تم توليد التقرير آلياً بواسطة {settings.app_name_ar} v{settings.version} — "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M')}_")
    add("")
    add("> **إخلاء مسؤولية:** هذا التقرير أداة تحليل فني/كمّي وليس نصيحة استثمارية. "
        "أسهم التقسيم العكسي عالية المخاطر (تخفيف، شطب محتمل، سيولة ضعيفة) — "
        "استخدم إدارة مخاطر صارمة ولا تخاطر بأكثر مما تتحمل خسارته.")
    add("")
    if sources:
        parts = [
            "%s (%s)" % (p.get("name"), "متصل" if p.get("ok") else "غير متصل")
            for p in sources.get("providers", [])
        ]
        add("_مصادر البيانات: " + "، ".join(parts) + "_")
    return "\n".join(lines)


def markdown_to_html(md: str, title: str = "تقرير تحليلي") -> str:
    """تحويل Markdown إلى HTML مكتمل مع تنسيق عربي (RTL) بدون مكتبات خارجية."""
    import html as _html
    import re

    lines = md.split("\n")
    out: list[str] = []
    in_table = False
    in_ul = False

    def close_all():
        nonlocal in_table, in_ul
        if in_table:
            out.append("</tbody></table></div>")
            in_table = False
        if in_ul:
            out.append("</ul>")
            in_ul = False

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            close_all()
            continue
        if line.startswith("| "):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue
            if not in_table:
                close_all()
                out.append('<div class="tw"><table><thead><tr>' +
                           "".join(f"<th>{_inline(c)}</th>" for c in cells) + "</tr></thead><tbody>")
                in_table = True
            else:
                out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
            continue
        if in_table:
            out.append("</tbody></table></div>")
            in_table = False
        if line.startswith("- "):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{_inline(line[2:])}</li>")
            continue
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if line.startswith("### "):
            out.append(f"<h4>{_inline(line[4:])}</h4>")
        elif line.startswith("## "):
            out.append(f"<h3>{_inline(line[3:])}</h3>")
        elif line.startswith("# "):
            out.append(f"<h2>{_inline(line[2:])}</h2>")
        elif line.startswith("> "):
            out.append(f'<blockquote>{_inline(line[2:])}</blockquote>')
        elif line.strip() == "---":
            out.append("<hr>")
        else:
            out.append(f"<p>{_inline(line)}</p>")
    close_all()

    body = "\n".join(out)
    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_html.escape(title)}</title>
<style>
  :root {{ --bg:#0d1117; --panel:#161b22; --border:#30363d; --txt:#e6edf3; --muted:#8b949e;
           --green:#3fb950; --red:#f85149; --amber:#d29922; --blue:#58a6ff; }}
  *{{box-sizing:border-box}}
  body{{background:var(--bg);color:var(--txt);font-family:"Segoe UI",Tahoma,Arial,sans-serif;
       margin:0;padding:32px;line-height:1.85;font-size:15px}}
  .wrap{{max-width:1050px;margin:0 auto}}
  h2{{color:var(--blue);border-bottom:2px solid var(--border);padding-bottom:10px;font-size:24px}}
  h3{{color:#e6edf3;margin-top:34px;font-size:19px;border-right:4px solid var(--blue);padding-right:10px}}
  h4{{color:var(--muted);font-size:16px;margin-top:22px}}
  table{{width:100%;border-collapse:collapse;margin:14px 0;font-size:14px;background:var(--panel);
        border-radius:8px;overflow:hidden}}
  th{{background:#1f2630;color:#c9d1d9;text-align:right;padding:10px 12px;border:1px solid var(--border);
     font-weight:600}}
  td{{padding:9px 12px;border:1px solid var(--border);vertical-align:top}}
  tr:nth-child(even) td{{background:#12171e}}
  .tw{{overflow-x:auto}}
  blockquote{{border-right:4px solid var(--amber);background:#1c1f24;margin:14px 0;padding:12px 16px;
             border-radius:6px;color:#d0d7de}}
  code{{background:#1f2630;padding:2px 6px;border-radius:4px}}
  strong{{color:#fff}}
  ul{{padding-right:22px}}
  li{{margin:5px 0}}
  hr{{border:0;border-top:1px solid var(--border);margin:26px 0}}
  p{{margin:9px 0}}
</style>
</head>
<body><div class="wrap">
{body}
</div></body></html>"""


def _inline(text: str) -> str:
    import html as _html
    import re
    t = _html.escape(text)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
    t = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", t)
    return t


def write_reports(symbols: list[str], hub: DataHub, out_dir: Path | None = None,
                  analyses: dict[str, eng.Analysis] | None = None) -> list[dict[str, Any]]:
    """كتابة تقارير Markdown + HTML لكل رمز، وإرجاع البيانات الوصفية للملفات."""
    out_dir = Path(out_dir or REPORTS_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    sources = hub.sources_report()
    written: list[dict[str, Any]] = []
    for sym in symbols:
        a = (analyses or {}).get(sym) or eng.analyze_symbol(sym, hub)
        md = analysis_to_markdown(a, sources)
        html = markdown_to_html(md, f"{a.symbol} — تقرير تحليلي")
        stamp = datetime.now().strftime("%Y%m%d")
        (out_dir / f"{a.symbol}_analysis_{stamp}.md").write_text(md, encoding="utf-8")
        (out_dir / f"{a.symbol}_analysis_{stamp}.html").write_text(html, encoding="utf-8")
        (out_dir / f"{a.symbol}_analysis_{stamp}.json").write_text(
            json.dumps(a.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        written.append({"symbol": a.symbol, "phase": a.phase, "score": a.score,
                        "md": str(out_dir / f"{a.symbol}_analysis_{stamp}.md"),
                        "html": str(out_dir / f"{a.symbol}_analysis_{stamp}.html")})
    return written


def watchlist_markdown(results: list[eng.Analysis], sources: dict | None = None) -> str:
    """تقرير موحّد لقائمة أفضل الفرص."""
    lines: list[str] = []
    add = lines.append
    add("# تقرير قائمة الفرص — استراتيجية التقسيم العكسي والانعكاس")
    add("")
    add(f"_تاريخ التوليد: {datetime.now().strftime('%Y-%m-%d %H:%M')} — "
        f"عدد الرموز المفحوصة: {len(results)}_")
    add("")
    elig = [a for a in results if a.eligible]
    watch = [a for a in results if a.watchlist and a.eligible]
    add(f"**فرص مؤهلة:** {len(elig)} | **في قائمة المتابعة (ثبات 3+ جلسات):** {len(watch)}")
    add("")
    add("## أفضل الفرص")
    add("")
    add("| الرمز | المرحلة | التقييم | السعر | التقسيم | صعود | هبوط | جلسات الثبات | RSI | الفلوت | بيع مكشوف | نقطة الاختراق | مخاطرة/عائد |")
    add("|-------|---------|---------|-------|---------|------|------|--------------|-----|--------|-----------|----------------|-------------|")
    for a in sorted(watch, key=lambda x: -x.score)[:25]:
        lv = a.levels or {}
        add(f"| **{a.symbol}** | {eng.phase_info(a.phase)['ar'].split('—')[-1].strip()} | {a.score} ({a.grade}) | "
            f"{_n(a.last_close,3)}$ | {a.rs_ratio_str} {a.rs_date or ''} | {_pct(a.spike_pct)} | "
            f"{_pct(a.decline_pct)} | {a.base_sessions} | {_n(a.rsi_trough,0)}→{_n(a.rsi_now,0)} | "
            f"{_m(a.float_shares)} | {_n(a.short_pct_float,1)}% | {_n(lv.get('trigger'),3)}$ | {_n(lv.get('rr_t2'),2)}:1 |")
    add("")
    add("## كل الرموز ذات التقسيم العكسي داخل النافذة")
    add("")
    add("| الرمز | المرحلة | التقييم | سبب الاستبعاد / الملاحظة |")
    add("|-------|---------|---------|--------------------------|")
    for a in sorted([x for x in results if x.rs_found], key=lambda x: (-x.phase, -x.score)):
        reason = " | ".join(a.rejections) if a.rejections else " | ".join(a.notes) or "مطابق للشروط"
        add(f"| {a.symbol} | {eng.phase_info(a.phase)['ar']} | {a.score} | {reason[:160]} |")
    add("")
    add("---")
    add("> إخلاء مسؤولية: أداة تحليل فني/كمّي وليست نصيحة استثمارية.")
    return "\n".join(lines)
