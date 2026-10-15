#!/usr/bin/env python3
"""مسح يومي تلقائي + بناء موقع لوحة جاهز للنشر (GitHub Pages) في خطوة واحدة.

ما الذي يفعله؟
  1) يمسح كوناً ديناميكياً ببيانات حقيقية فقط؛ الفشل لا يُستبدل بالديمو.
  2) يحفظ النتائج:  site/data/latest.json · site/data/latest.csv · site/history/<وقت>.json
  3) يولّد تقارير تحليلية (Markdown + HTML + JSON) لأهم الرموز في site/reports/.
  4) يبني اللوحة التفاعلية الثابتة site/dashboard.html (ملف واحد يعمل بلا مخدم وبلا إنترنت).
  5) يبني صفحة هبوط عربية site/index.html فيها آخر تحديث والملخّص وقائمة المتابعة وروابط التقارير.

الاستخدام:
    python3 tools/daily_scan.py --site site --limit 40 --mode live
    python3 tools/daily_scan.py --site /tmp/site --limit 8 --mode demo --test-only --no-dashboard   # سريع للاختبار
"""
from __future__ import annotations

import argparse
import csv
import html
import importlib.util
import json
import socket
import sys
import threading
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings                                 # noqa: E402
from app.providers.hub import get_hub                           # noqa: E402
from app.reporting import markdown_to_html, watchlist_markdown, write_reports   # noqa: E402
from app.strategy import engine as eng                          # noqa: E402
from app.strategy.strategy_engine import StrategyConfig, WEIGHT_LABELS_AR   # noqa: E402

RIYADH = timezone(timedelta(hours=3))
REPO_SLUG = "omermasaad75-alt/-penny-stock-news-bot"
DEFAULT_PAGES = "https://omermasaad75-alt.github.io/-penny-stock-news-bot/"

# نقاط دورة الحياة في الجدول = معايير الاستراتيجية الستة (انظر engine.CHECKLIST)
CHECK_SHORT = {
    "pattern": "قاع ثانٍ / سحب سيولة",
    "confirm": "شمعة تأكيد",
    "drop_zone": "القمة ÷ 2",
    "negative": "هبوط 40%+",
    "price": "السعر 1.5–2",
    "resistance": "مقاومة 20%+",
}

CSV_COLUMNS = [
    ("symbol", "الرمز"), ("name", "الاسم"), ("phase", "المرحلة"), ("phase_ar", "الحالة"),
    ("eligible", "فرصة مؤهلة"), ("watchlist", "قائمة المتابعة"),
    ("rejections", "أسباب الاستبعاد"),
    ("score", "التقييم"), ("grade", "الدرجة"),
    ("last_close", "آخر إغلاق"), ("drop_zone", "منطقة القمة÷2"),
    ("pattern_low1", "القاع الأول"), ("pattern_low2", "القاع الثاني"),
    ("entry", "الدخول"), ("stop", "الوقف"), ("target_1", "الهدف 1 (+25%)"),
    ("position_size", "حجم المركز (أسهم)"), ("risk_per_share", "مخاطرة السهم"),
    ("short_shares", "الشورت (أسهم)"),
    ("last_close_time", "وقت آخر شمعة"),
    ("synthetic", "بيانات تجريبية"),
    ("price_source", "مصدر الأسعار"), ("fundamentals_source", "مصدر الأساسيات"),
    ("news_source", "مصدر الأخبار"),
]


# --------------------------------------------------------------------------- #
#  أدوات مساعدة
# --------------------------------------------------------------------------- #
def now_riyadh() -> datetime:
    return datetime.now(RIYADH)


def fmt_riyadh(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


def _f(x, nd=3, dash="—"):
    if x is None:
        return dash
    try:
        return f"{float(x):,.{nd}f}"
    except (TypeError, ValueError):
        return str(x)


def _pct(x, nd=1, dash="—"):
    if x is None:
        return dash
    return f"{float(x):+,.{nd}f}%"


def _plan(a) -> dict:
    return (a.trade_plan or {}) if a.trade_plan and a.trade_plan.get("valid") else {}


def dots_html(a) -> str:
    checks = a.lifecycle_checks or []
    if not checks:
        return '<span class="dots"><i class="dot muted"></i></span>'
    out = []
    for c in checks:
        cls = "ok" if c.get("ok") else "pending"
        title = html.escape(str(c.get("ar") or c.get("key") or ""))
        out.append(f'<i class="dot {cls}" title="{title}"></i>')
    return '<span class="dots">' + "".join(out) + "</span>"


def summarize(results: list) -> dict:
    elig = [a for a in results if a.eligible]
    watch = [a for a in results if a.watchlist]
    ready = [a for a in results if a.strategy_status == "READY"]
    near = [a for a in results if a.strategy_status == "NEAR"]
    failed = [a for a in results if a.lifecycle_failed]
    return {
        "scanned": len(results),
        "eligible": len(elig),
        "watchlist": len(watch),
        "ready": len(ready),
        "near": len(near),
        "failed": len(failed),
        "avg_score_watchlist": round(sum(a.score for a in watch) / len(watch), 1) if watch else 0,
        "top_symbol": watch[0].symbol if watch else "",
        "eligible_symbols": [a.symbol for a in elig],
        "ready_symbols": [a.symbol for a in ready],
        "watchlist_symbols": [a.symbol for a in watch],
    }


def build_coverage(sources: dict, results: list) -> dict:
    """تفصيل صريح لحدود التغطية: ما لم يُفحص وما لم تتوفر بياناته."""
    scan_cov = sources.get("scan_coverage") or {}
    disc = sources.get("candidate_discovery") or {}
    excluded = sources.get("rejections") or {}

    no_data: list[str] = []
    no_split: list[str] = []
    for sym, reason in excluded.items():
        if "لا تقسيم" in str(reason or ""):
            no_split.append(sym)
        else:
            no_data.append(sym)   # بيانات غير متاحة / أسعار قديمة / غير قابل للتحقق

    watch = [a.symbol for a in results if a.watchlist]
    ready = [a.symbol for a in results if a.strategy_status == "READY"]

    checked = int(scan_cov.get("candidates_checked") or sources.get("candidates_checked") or 0)
    collected = int(scan_cov.get("candidates_collected") or disc.get("candidates_collected") or 0) or checked
    unchecked = int(scan_cov.get("candidates_unchecked") or max(collected - checked, 0))
    partial = bool(scan_cov.get("partial") or disc.get("truncated"))
    return {
        "candidates_collected": collected,
        "candidates_checked": checked,
        "candidates_unchecked": unchecked,
        "partial": partial,
        "stop_reason": str(scan_cov.get("stop_reason") or ""),
        "total_matching_screener": disc.get("total_matching_screener"),
        "price_ceiling": disc.get("price_ceiling"),
        "pages_fetched": disc.get("pages_fetched", 0),
        "discovery_truncated": bool(disc.get("truncated")),
        "categories": {
            "unchecked": {"count": unchecked,
                          "symbols": list(scan_cov.get("unchecked_symbols") or [])[:20]},
            "no_data": {"count": len(no_data), "symbols": no_data[:20]},
            "no_recent_split": {"count": len(no_split), "symbols": no_split[:20]},
            "watchlist": {"count": len(watch), "symbols": watch[:20]},
            "ready": {"count": len(ready), "symbols": ready[:20]},
        },
    }


# --------------------------------------------------------------------------- #
#  1) المسح
# --------------------------------------------------------------------------- #
def run_scan(mode: str | None, limit: int, symbols: list[str] | None = None):
    hub = get_hub(mode=mode, refresh=bool(mode))
    syms = symbols or hub.universe(limit=limit)
    print(f"▶ وضع البيانات المطلوب: {mode or settings.data_mode} — الرموز: {len(syms)}", flush=True)
    results: list = []
    t0 = time.time()
    for i, sym in enumerate(syms, 1):
        try:
            a = eng.analyze_symbol(sym, hub)
        except Exception as exc:                                   # noqa: BLE001
            a = eng.Analysis(symbol=sym)
            eng.add_rejection(a, "analysis_error", f"خطأ: {exc}")
        if hub.mode == "live" and (not a.price_source or not a.fundamentals_source or a.last_close <= 0):
            hub._reject(sym, "لا توجد بيانات حقيقية لهذا الرمز — " + "؛ ".join(a.rejections))
            continue
        results.append(a)
        print(f"  [{i}/{len(syms)}] {sym:<8} مرحلة={a.phase} تقييم={a.score}", flush=True)
    results.sort(key=lambda x: (-x.phase, -x.score))
    sources = hub.sources_report()
    sources["candidates_checked"] = getattr(hub.yahoo, "candidates_checked", len(syms))
    sources["verified_symbols"] = getattr(hub.yahoo, "verified_symbols", [a.symbol for a in results])
    sources["coverage"] = build_coverage(sources, results)
    print(f"✔ انتهى المسح في {time.time() - t0:,.1f} ثانية — الوضع الفعّال: {sources['mode_effective']}",
          flush=True)
    cov = sources["coverage"]
    print(f"▶ التغطية: مرشحون {cov['candidates_collected']} · مفحوصون {cov['candidates_checked']} · "
          f"غير مفحوصين {cov['candidates_unchecked']}"
          + (" · التغطية جزئية ⚠" if cov["partial"] else ""), flush=True)
    return results, sources


# --------------------------------------------------------------------------- #
#  2) حفظ البيانات
# --------------------------------------------------------------------------- #
def write_dataset(site: Path, results: list, sources: dict, mode: str, started: datetime) -> dict:
    (site / "data").mkdir(parents=True, exist_ok=True)
    (site / "history").mkdir(parents=True, exist_ok=True)
    stamp = started.strftime("%Y-%m-%d_%H%M")
    summary = summarize(results)
    rows = [a.to_dict() for a in results]
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generated_at_riyadh": fmt_riyadh(started),
        "stamp": stamp,
        "mode": sources.get("mode_effective", mode),
        "mode_effective": sources.get("mode_effective", mode),
        "synthetic_count": sum(a.synthetic for a in results),
        "excluded": sources.get("rejections", {}),
        "mode_setting": sources.get("mode_setting", mode),
        "summary": summary,
        "strategy": {"params": asdict(StrategyConfig()), "weights_ar": WEIGHT_LABELS_AR},
        "coverage": sources.get("coverage", {}),
        "sources": sources,
        "rows": rows,
    }
    (site / "data" / "latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    # CSV بعناوين عربية (سهل الفتح في Excel)
    with (site / "data" / "latest.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow([ar for _, ar in CSV_COLUMNS])
        for a in results:
            plan = _plan(a)
            pat = a.pattern or {}
            look = {
                "symbol": a.symbol, "name": a.name, "phase": a.phase, "phase_ar": a.phase_ar,
                "eligible": "نعم" if a.eligible else "لا",
                "watchlist": "نعم" if a.watchlist else "لا",
                "rejections": " | ".join(a.rejections),
                "score": a.score, "grade": a.grade, "last_close": a.last_close,
                "drop_zone": (a.drop_zone or {}).get("zone"),
                "pattern_low1": pat.get("low1"), "pattern_low2": pat.get("low2"),
                "entry": plan.get("entry"), "stop": plan.get("stop"),
                "target_1": (plan.get("targets") or [None])[0],
                "position_size": plan.get("position_size"), "risk_per_share": plan.get("risk_per_share"),
                "short_shares": a.short_shares,
                "last_close_time": a.as_of or "",
                "synthetic": "نعم" if a.synthetic else "لا",
                "price_source": a.price_source or "—",
                "fundamentals_source": a.fundamentals_source or "—",
                "news_source": a.news_source or "—",
            }
            w.writerow([look.get(key, "") for key, _ in CSV_COLUMNS])

    # سجل تاريخي مختصر
    hist_entry = {
        "stamp": stamp,
        "generated_at_riyadh": payload["generated_at_riyadh"],
        "mode": payload["mode"],
        "summary": summary,
        "top": [
            {"symbol": a.symbol, "phase": a.phase, "status": a.strategy_status, "score": a.score,
             "grade": a.grade, "close": a.last_close, "eligible": a.eligible, "watchlist": a.watchlist,
             "synthetic": a.synthetic, "price_source": a.price_source,
             "fundamentals_source": a.fundamentals_source, "news_source": a.news_source}
            for a in results[:15]
        ],
    }
    (site / "history" / f"{stamp}.json").write_text(
        json.dumps(hist_entry, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    idx_path = site / "history" / "index.json"
    try:
        index = json.loads(idx_path.read_text(encoding="utf-8")) if idx_path.exists() else []
    except Exception:                                              # noqa: BLE001
        index = []
    index = [e for e in index if e.get("stamp") != stamp]
    index.insert(0, {"stamp": stamp, "generated_at_riyadh": payload["generated_at_riyadh"],
                     "mode": payload["mode"], "summary": summary})
    index = index[:400]
    idx_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    payload["history"] = index[:20]
    print(f"✔ البيانات: latest.json / latest.csv / history/{stamp}.json", flush=True)
    return payload


# --------------------------------------------------------------------------- #
#  3) التقارير
# --------------------------------------------------------------------------- #
def build_reports(site: Path, results: list, hub, top_n: int) -> list[dict]:
    out_dir = site / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    picks = [a.symbol for a in results if a.watchlist]
    if len(picks) < min(top_n, len(results)):
        picks += [a.symbol for a in results if a.symbol not in picks]
    targets = picks[:top_n]
    written = write_reports(targets, hub, out_dir, analyses={a.symbol: a for a in results})
    try:
        wl_md = watchlist_markdown(results, hub.sources_report())
        day = now_riyadh().strftime("%Y%m%d")
        (out_dir / f"watchlist_{day}.md").write_text(wl_md, encoding="utf-8")
        (out_dir / f"watchlist_{day}.html").write_text(
            markdown_to_html(wl_md, "قائمة المتابعة — الاستراتيجية"), encoding="utf-8")
    except Exception as exc:                                       # noqa: BLE001
        print(f"تعذّر توليد تقرير قائمة المتابعة: {exc}", flush=True)
    print(f"✔ التقارير: {len(written)} رمز في {out_dir}", flush=True)
    return written


# --------------------------------------------------------------------------- #
#  4) اللوحة التفاعلية الثابتة (ملف واحد)
# --------------------------------------------------------------------------- #
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _serve(api, port: int, timeout: float = 90.0):
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(api, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, name="daily-scan-api", daemon=True).start()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return server
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("تعذّر تشغيل المخدم الداخلي")


def build_dashboard(site: Path, results: list, sources: dict, hub, max_symbols: int,
                    port: int | None = None, live: bool = False) -> Path | None:
    """يبني ملف لوحة واحد يعمل بلا مخدم (نفس ملف النسخة الثابتة)."""
    try:
        import uvicorn  # noqa: F401
    except Exception as exc:                                       # noqa: BLE001
        print(f"تخطّي اللوحة الثابتة (uvicorn غير متاح): {exc}", flush=True)
        return None

    from app.server import SCAN, app as api

    SCAN.results = results
    SCAN.by_symbol = {a.symbol: a for a in results}
    SCAN.sources = sources
    with SCAN.lock:
        SCAN.state.update({
            "running": False, "progress": len(results), "total": len(results), "current": "",
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "error": None, "mode": sources.get("mode_effective"),
        })

    spec = importlib.util.spec_from_file_location("make_snapshot", ROOT / "tools" / "make_snapshot.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                                   # type: ignore[union-attr]

    port = port or _free_port()
    server = _serve(api, port)
    out = site / "dashboard.html"
    try:
        badge = "نسخة ثابتة — بيانات حقيقية" if live else "نسخة ثابتة — بيانات تجريبية"
        mod.build(f"http://127.0.0.1:{port}", out, max_symbols, badge=badge)
    finally:
        server.should_exit = True
        time.sleep(0.5)
    return out


# --------------------------------------------------------------------------- #
#  5) صفحة الهبوط
# --------------------------------------------------------------------------- #
LANDING = Template("""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>لوحة الاستراتيجية — قاع ثانٍ وقمة ÷ 2</title>
<style>
  :root{--bg:#0d1117;--panel:#161b22;--border:#30363d;--txt:#e6edf3;--muted:#8b949e;
        --green:#3fb950;--amber:#d29922;--red:#f85149;--blue:#58a6ff}
  *{box-sizing:border-box}
  body{background:var(--bg);color:var(--txt);font-family:"Segoe UI",Tahoma,Arial,sans-serif;
       margin:0;padding:24px;line-height:1.7}
  .wrap{max-width:1100px;margin:0 auto}
  h1{font-size:22px;margin:0 0 4px} h2{font-size:17px;margin:26px 0 8px;color:var(--blue)}
  .muted{color:var(--muted);font-size:13px}
  .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:16px 0}
  .kpi{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:12px}
  .kpi span{display:block;color:var(--muted);font-size:12px} .kpi b{font-size:22px}
  table{width:100%;border-collapse:collapse;background:var(--panel);border-radius:10px;overflow:hidden;font-size:13px}
  th,td{padding:8px 10px;border-bottom:1px solid var(--border);text-align:right;vertical-align:top}
  th{background:#1f2630;color:#c9d1d9;font-weight:600}
  .tw{overflow-x:auto}
  .dots{display:inline-flex;gap:3px}.dot{width:9px;height:9px;border-radius:50%;display:inline-block;background:#30363d}
  .dot.ok{background:var(--green)} .dot.pending{background:#30363d}
  .c-green{color:var(--green)} .c-amber{color:var(--amber)} .c-red{color:var(--red)}
  .note{background:var(--panel);border:1px solid var(--border);border-right:4px solid var(--amber);
        border-radius:8px;padding:10px 14px;margin:12px 0;font-size:13px}
  a{color:var(--blue)} .links a{margin-left:14px}
</style>
</head>
<body><div class="wrap">
<h1>لوحة الاستراتيجية — قاع ثانٍ · قمة ÷ 2 · أهداف 4×25%</h1>
<div class="muted">آخر تحديث: $generated (توقيت الرياض) · الوضع: $mode_label · $providers</div>
<div class="links" style="margin-top:8px">
  <a href="dashboard.html?v=$stamp">اللوحة التفاعلية</a>
  <a href="data/latest.csv">تحميل CSV</a>
  <a href="data/latest.json">بيانات JSON</a>
  <a href="$actions_url">سجل التشغيل</a>
</div>

<div class="kpis">
  <div class="kpi"><span>أسهم تم فحصها</span><b>$scanned</b></div>
  <div class="kpi"><span>جاهز للدخول</span><b class="c-green">$ready</b></div>
  <div class="kpi"><span>قريب من الاكتمال</span><b class="c-amber">$near</b></div>
  <div class="kpi"><span>قائمة المتابعة</span><b>$watchlist</b></div>
</div>

<div class="note"><b>تغطية المسح:</b> مرشحون $collected · مفحوصون $checked · غير مفحوصين $unchecked$partial_note</div>

<h2>قائمة المتابعة</h2>
<div class="tw"><table>
<thead><tr><th>الرمز</th><th>المعايير</th><th>الحالة</th><th>الدرجة</th><th>السعر</th>
<th>دخول</th><th>وقف</th><th>أهداف (+25% كل)</th><th>التحليل</th></tr></thead>
<tbody>
$rows_html
</tbody></table></div>

<div class="note">$disclaimer</div>
<div class="muted">مصادر البيانات: $providers_list</div>
</div></body></html>
""")


def build_landing(site: Path, payload: dict, written: list[dict]) -> Path:
    summary = payload["summary"]
    cov = payload.get("coverage", {}).get("categories", {})
    report_names = {w["symbol"]: Path(w["html"]).name for w in written if w.get("html")}
    rows = [a for a in payload.get("rows") or [] if a.get("watchlist")]
    rows_html = []
    for r in rows[:60]:
        plan = r.get("trade_plan") or {}
        valid = plan.get("valid")
        checks = r.get("lifecycle_checks") or []
        dots = "".join(f'<i class="dot {"ok" if c.get("ok") else "pending"}"></i>' for c in checks)
        done = sum(1 for c in checks if c.get("ok"))
        rep = report_names.get(r["symbol"])
        link = f'<a href="reports/{rep}">تحليل ›</a>' if rep else "—"
        rows_html.append(
            "<tr>"
            f'<td><b>{html.escape(r["symbol"])}</b><div class="muted">{html.escape((r.get("name") or "")[:22])}</div></td>'
            f'<td><span class="dots">{dots}</span> <span class="muted">{done}/6</span></td>'
            f'<td>{html.escape(r.get("phase_ar") or "")}</td>'
            f'<td>{r.get("score")} {html.escape(str(r.get("grade") or ""))}</td>'
            f'<td>{_f(r.get("last_close"))}</td>'
            f'<td>{_f(plan.get("entry")) if valid else "—"}</td>'
            f'<td>{_f(plan.get("stop")) if valid else "—"}</td>'
            f'<td>{" / ".join(_f(t) for t in plan.get("targets", [])) if valid else "—"}</td>'
            f"<td>{link}</td>"
            "</tr>")
    if not rows_html:
        rows_html.append('<tr><td colspan="9" class="muted">لا توجد أسهم في قائمة المتابعة لهذا المسح.</td></tr>')

    mode = payload.get("mode", "demo")
    providers = payload.get("sources", {}).get("providers", [])
    providers_list = "، ".join(
        f"{p.get('name')} ({'متصل' if p.get('ok') else 'غير متصل'})" for p in providers) or "—"
    partial = cov.get("unchecked", {}).get("count", 0)
    ctx = {
        "generated": payload["generated_at_riyadh"],
        "mode_label": "حقيقي (live)" if mode == "live" else f"{mode} — غير منشور",
        "providers": html.escape(providers_list),
        "providers_list": html.escape(providers_list),
        "stamp": payload["stamp"],
        "actions_url": f"https://github.com/{REPO_SLUG}/actions/workflows/daily-scan.yml",
        "scanned": summary["scanned"], "ready": summary["ready"], "near": summary["near"],
        "watchlist": summary["watchlist"],
        "collected": payload.get("coverage", {}).get("candidates_collected", "—"),
        "checked": payload.get("coverage", {}).get("candidates_checked", "—"),
        "unchecked": partial,
        "partial_note": " · <b class=\"c-amber\">التغطية جزئية</b>"
        if payload.get("coverage", {}).get("partial") else "",
        "rows_html": "\n".join(rows_html),
        "disclaimer": "تنبيه: هذا النظام أداة تحليل فني وليست نصيحة مالية — القرار النهائي للمستثمر. "
                      "أسهم الشركات الصغيرة عالية المخاطر.",
    }
    out = site / "index.html"
    out.write_text(LANDING.substitute(ctx), encoding="utf-8")
    print(f"✔ صفحة الهبوط: {out}", flush=True)
    return out


def write_manifest(site: Path, stamp: str) -> None:
    import shutil
    icons_dir = site / "icons"
    icons_dir.mkdir(parents=True, exist_ok=True)
    src_icons = ROOT / "web" / "icons"
    if src_icons.exists():
        for icon in src_icons.glob("*.png"):
            shutil.copy2(icon, icons_dir / icon.name)
    (site / "manifest.json").write_text(json.dumps({
        "name": "لوحة الاستراتيجية — قاع ثانٍ وقمة ÷ 2",
        "short_name": "Strategy",
        "description": "مسح يومي بالاستراتيجية — بيانات حقيقية من Yahoo/SEC/FINRA",
        "start_url": "./dashboard.html",
        "display": "standalone",
        "background_color": "#080b11",
        "theme_color": "#080b11",
        "dir": "rtl", "lang": "ar",
        "icons": [
            {"src": "icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "icons/apple-touch-icon.png", "sizes": "180x180", "type": "image/png",
             "purpose": "any maskable"},
        ],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    (site / ".nojekyll").write_text("", encoding="utf-8")
    favicon_src = src_icons / "favicon-32.png" if src_icons.exists() else None
    if favicon_src and favicon_src.exists():
        shutil.copy2(favicon_src, site / "favicon.png")


# --------------------------------------------------------------------------- #
#  التشغيل
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="مسح يومي + بناء موقع اللوحة")
    ap.add_argument("--site", default="site", help="مجلد الموقع الناتج (يُنشر على GitHub Pages)")
    ap.add_argument("--limit", type=int, default=40, help="عدد الرموز")
    ap.add_argument("--test-only", action="store_true", help="السماح بالديمو للاختبارات فقط؛ محظور في Actions")
    ap.add_argument("--mode", default="live", help="live افتراضياً؛ demo مع --test-only فقط")
    ap.add_argument("--symbols", default="", help="رموز محددة مفصولة بفواصل (تجاوز الكون)")
    ap.add_argument("--top-reports", type=int, default=12, help="عدد التقارير المولّدة")
    ap.add_argument("--dashboard-symbols", type=int, default=40, help="عدد الرموز داخل اللوحة الثابتة")
    ap.add_argument("--no-dashboard", action="store_true", help="تخطّي بناء اللوحة الثقيلة (اختبار سريع)")
    args = ap.parse_args()

    import os
    if args.mode != "live" and (not args.test_only or os.environ.get("GITHUB_ACTIONS") == "true"):
        print("✖ رفض النشر: البيانات التجريبية محظورة؛ استخدم --mode live.", flush=True)
        return 4

    started = now_riyadh()
    site = Path(args.site)
    if not site.is_absolute():
        site = ROOT / site
    site.mkdir(parents=True, exist_ok=True)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or None
    requested = (args.mode or settings.data_mode or "demo").lower()
    if requested in ("live", "auto"):
        print(f"▶ وضع {requested}: البيانات الحقيقية مطلوبة — لن تُنشر أي بيانات تجريبية.", flush=True)
    elif requested == "demo":
        print("⚠ وضع DEMO مُختار — للاختبار المحلي فقط. لن يُقبل هذا الوضع في سير عمل النشر.",
              flush=True)

    results, sources = run_scan(args.mode, args.limit, symbols)
    mode_eff = sources.get("mode_effective", "demo")
    synth_rows = sum(1 for a in results if a.synthetic)

    if requested in ("live", "auto"):
        if mode_eff != "live":
            print(f"✖ فشل: وضع البيانات الفعّال هو '{mode_eff}' وليس 'live' — تعذّر الوصول لمزوّدي "
                  f"البيانات الحقيقية. لن تُنشر بيانات تجريبية.", flush=True)
            return 3
        if synth_rows > 0:
            print(f"✖ فشل: وُجد {synth_rows} صف ببيانات تجريبية في الوضع الحي — "
                  f"هذا تسريب غير مسموح به.", flush=True)
            return 4
        print("✔ تحقق الوضع: كل البيانات حقيقية (mode_effective=live، synthetic=0).", flush=True)

    if requested == "live":
        from tools.validate_live import validate
        errors = validate({"mode_effective": mode_eff, "rows": [a.to_dict() for a in results]})
        if errors:
            print("✖ رفض النشر: " + "؛ ".join(errors), flush=True)
            return 4

    if not results and not (requested == "live" and getattr(get_hub().yahoo, "discovery_ok", False)):
        print("✖ لا نتائج حقيقية ولا اكتشاف موثوق للكون — لن ننشر.", flush=True)
        return 2
    print(f"▶ أسهم في قائمة المتابعة: {sum(1 for a in results if a.watchlist)} / {len(results)}", flush=True)

    payload = write_dataset(site, results, sources, args.mode or settings.data_mode, started)
    hub = get_hub()
    written = build_reports(site, results, hub, args.top_reports)

    if not args.no_dashboard:
        dash = build_dashboard(site, results, sources, hub, args.dashboard_symbols,
                               live=sources.get("mode_effective") == "live")
        if dash:
            print(f"✔ اللوحة التفاعلية: {dash} ({dash.stat().st_size / 1e6:.2f} MB)", flush=True)

    write_manifest(site, payload["stamp"])
    build_landing(site, payload, written)
    total = sum(f.stat().st_size for f in site.rglob("*") if f.is_file())
    print(f"✔ اكتمل بناء الموقع: {site} — {total / 1e6:.2f} MB إجمالاً", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
