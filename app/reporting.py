"""مولّد التقارير — تقرير تحليلي (Markdown + HTML) لكل سهم أو لقائمة المتابعة."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import REPORTS_DIR, settings
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


def analysis_to_markdown(a: eng.Analysis, sources: dict | None = None) -> str:
    """تقرير Markdown للسهم: المعايير، النمط، منطقة الهبوط، خطة الصفقة، الدرجة."""
    lines: list[str] = []

    def add(line: str = "") -> None:
        lines.append(line)

    plan = a.trade_plan or {}
    lv = a.levels or {}
    pat = a.pattern or {}
    dz = a.drop_zone or {}

    add(f"# {a.symbol} — {a.name or a.symbol}")
    add("")
    add(f"**الحالة:** {a.phase_ar}  ·  **الدرجة:** {a.score}/100 ({a.grade})  ·  **آخر جلسة:** {a.as_of or '—'}")
    add("")
    if a.synthetic:
        add("> ⚠ **بيانات تجريبية (demo)** — لا تُستخدم للقرار.")
        add("")
    add(f"> {a.action_ar}")
    add("")

    add("## 1) المعايير")
    add("")
    add("| المعيار | الدرجة | النقاط | الوزن |")
    add("|---------|--------|--------|-------|")
    for b in a.breakdown:
        add(f"| {b['ar']} | {b['score']}% | {b['points']} | {b['weight']} |")
    add(f"| **المجموع** | | **{a.score}** | **100** |")
    add("")

    add("## 2) النمط والتأكيد")
    add("")
    if pat.get("found"):
        label = "سحب سيولة" if pat.get("type") == "liquidity_sweep" else "قاع ثانٍ (دعم ثابت)"
        add(f"- **النمط:** {label}")
        add(f"- **القاع الأول:** {_n(pat.get('low1'), 3)}$  ·  **القاع الثاني:** {_n(pat.get('low2'), 3)}$")
        add(f"- **خط العنق:** {_n(pat.get('neckline'), 3)}$")
    elif pat.get("broken"):
        add("- النمط موجود لكنه **منكسر** (السعر تحت وقف القاع الثاني).")
    else:
        add("- لم يظهر قاع ثانٍ أو سحب سيولة بعد.")
    conf = a.confirmation or {}
    add(f"- **شمعة التأكيد:** {'✅ مكتملة' if conf.get('confirmed') else '⏳ بانتظار'}")
    add("")

    add("## 3) منطقة الهبوط (القمة ÷ 2)")
    add("")
    if dz:
        add(f"- **قمة الصعود:** {_n(dz.get('spike_high'), 3)}$")
        add(f"- **المنطقة المرجعية:** {_n(dz.get('zone'), 3)}$  (من {_n(dz.get('zone_low'), 3)}$ إلى {_n(dz.get('zone_high'), 3)}$)")
    else:
        add("- لا توجد بيانات كافية.")
    add("")

    add("## 4) خطة الصفقة")
    add("")
    if plan.get("valid"):
        add("| البند | القيمة |")
        add("|-------|--------|")
        add(f"| الدخول | {_n(plan.get('entry'), 3)}$ |")
        add(f"| الوقف (3% تحت القاع الثاني) | {_n(plan.get('stop'), 3)}$ |")
        add(f"| مخاطرة السهم | {_n(plan.get('risk_per_share'), 3)}$ |")
        add(f"| حجم المركز | {plan.get('position_size', 0):,} سهم |")
        for i, t in enumerate(plan.get("targets", []), 1):
            add(f"| الهدف {i} (+{25 * i}%) | {_n(t, 3)}$ |")
    else:
        add("- لا توجد خطة صفقة صالحة حالياً.")
    add("")

    add("## 5) المؤشرات الفنية")
    add("")
    add("| المؤشر | القيمة |")
    add("|--------|--------|")
    add(f"| RSI (الحالي / أدنى 40 شمعة) | {_n(a.rsi_now, 1)} / {_n(a.rsi_trough, 1)} |")
    add(f"| MACD Hist | {_n(a.macd_hist, 4)}{' ↑' if a.macd_turning else ''} |")
    add(f"| RVOL | {_n(a.rvol, 2)}x |")
    add(f"| جلسات إغلاق فوق EMA9 | {a.above_ema9_sessions or 0} |")
    add(f"| القناة الهابطة | {'مخترقة' if a.channel_broken else 'لم تُخترق'} |")
    add(f"| قيعان صاعدة (آخر 25 شمعة) | {a.higher_lows or 0} |")
    add("")
    add("### النماذج الفنية المرصودة")
    add("")
    patterns = list(a.patterns_daily or []) + list(a.patterns_4h or [])
    if not patterns:
        add("- لا توجد نماذج فنية مؤكدة حالياً.")
    for pt in patterns:
        status = "مؤكد" if pt.get("status") == "confirmed" else "قيد التشكّل"
        add(f"- {pt.get('name_ar', '')} ({pt.get('tf', '')}) — ثقة {round((pt.get('confidence') or 0) * 100)}% — {status}")
    add("")
    add("## 6) المؤشرات الأساسية")
    add("")
    add("| المؤشر | القيمة |")
    add("|--------|--------|")
    add(f"| الشورت (أسهم) | {_n(a.short_shares, 0)} |")
    add(f"| الفلوت | {_n(a.float_shares, 0)} |")
    add(f"| القيمة المتداولة اليومية | {_n(a.dollar_volume, 0)}$ |")
    add(f"| ATR % | {_n(a.atr_pct, 2)} |")
    add("")

    if a.notes or a.rejections:
        add("## 7) ملاحظات وأسباب الاستبعاد")
        add("")
        for r in a.rejections:
            add(f"- ✖ {r}")
        for note in a.notes:
            add(f"- {note}")
        add("")

    add("---")
    add("")
    add(f"_تم توليد التقرير آلياً بواسطة {settings.app_name_ar} v{settings.version} — "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M')}_")
    add("")
    add("> **إخلاء مسؤولية:** هذا التقرير أداة تحليل فني/كمّي وليس نصيحة استثمارية. "
        "أسهم الشركات الصغيرة عالية المخاطر — استخدم إدارة مخاطر صارمة.")
    add("")
    if sources:
        parts = [
            "%s (%s)" % (p.get("name"), "متصل" if p.get("ok") else "غير متصل")
            for p in sources.get("providers", [])
        ]
        add("_مصادر البيانات: " + "، ".join(parts) + "_")
    return "\n".join(lines)


def watchlist_markdown(results: list[eng.Analysis], sources: dict | None = None) -> str:
    """قائمة المتابعة: الأسهم READY / NEAR / WATCH مرتبة حسب المرحلة ثم الدرجة."""
    lines: list[str] = []

    def add(line: str = "") -> None:
        lines.append(line)

    rows = sorted([a for a in results if a.watchlist], key=lambda x: (-x.phase, -x.score))
    add("# قائمة المتابعة")
    add("")
    add(f"_تاريخ المسح: {datetime.now().strftime('%Y-%m-%d %H:%M')}_")
    add("")
    add(f"**عدد الأسهم:** {len(rows)}  ·  **جاهز للدخول:** {sum(1 for a in rows if a.phase == 6)}  ·  "
        f"**قريب من الاكتمال:** {sum(1 for a in rows if a.phase == 5)}  ·  **مراقبة:** {sum(1 for a in rows if a.phase == 2)}")
    add("")
    add("| الرمز | الحالة | الدرجة | السعر | دخول | وقف | هدف 1 | مخاطرة/سهم |")
    add("|-------|--------|--------|-------|------|-----|-------|------------|")
    for a in rows:
        plan = a.trade_plan or {}
        valid = plan.get("valid")
        add(f"| **{a.symbol}** | {a.phase_ar} | {a.score} ({a.grade}) | {_n(a.last_close, 3)}$ | "
            f"{_n(plan.get('entry'), 3) if valid else '—'} | {_n(plan.get('stop'), 3) if valid else '—'} | "
            f"{_n(plan['targets'][0], 3) if valid else '—'} | {_n(plan.get('risk_per_share'), 3) if valid else '—'} |")
    if not rows:
        add("| — | لا توجد أسهم في قائمة المتابعة لهذا المسح | — | — | — | — | — | — |")
    add("")
    add("> قائمة المتابعة ليست توصية شراء. راجع تقرير كل سهم قبل أي قرار.")
    if sources:
        parts = [
            "%s (%s)" % (p.get("name"), "متصل" if p.get("ok") else "غير متصل")
            for p in sources.get("providers", [])
        ]
        add("")
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
        if line.startswith("|"):
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
                        "eligible": a.eligible, "watchlist": a.watchlist,
                        "status": a.strategy_status,
                        "md": str(out_dir / f"{a.symbol}_analysis_{stamp}.md"),
                        "html": str(out_dir / f"{a.symbol}_analysis_{stamp}.html")})
    return written
