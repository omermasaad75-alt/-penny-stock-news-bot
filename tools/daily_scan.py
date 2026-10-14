#!/usr/bin/env python3
"""مسح يومي تلقائي + بناء موقع لوحة جاهز للنشر (GitHub Pages) في خطوة واحدة.

ما الذي يفعله؟
  1) يمسح الكون (وضع auto: بيانات حقيقية أولاً، ثم تراجع تلقائي للتجريبي عند فشل الاتصال).
  2) يحفظ النتائج:  site/data/latest.json · site/data/latest.csv · site/history/<وقت>.json
  3) يولّد تقارير تحليلية (Markdown + HTML + JSON) لأهم الرموز في site/reports/.
  4) يبني اللوحة التفاعلية الثابتة site/dashboard.html (ملف واحد يعمل بلا مخدم وبلا إنترنت).
  5) يبني صفحة هبوط عربية site/index.html فيها آخر تحديث والملخّص وأفضل الفرص وروابط التقارير.

الاستخدام:
    python3 tools/daily_scan.py --site site --limit 40 --mode auto
    python3 tools/daily_scan.py --site /tmp/site --limit 8 --mode demo --no-dashboard   # سريع للاختبار
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
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import STRATEGY, settings                      # noqa: E402
from app.providers.hub import get_hub                          # noqa: E402
from app.reporting import markdown_to_html, watchlist_markdown, write_reports   # noqa: E402
from app.strategy import engine as eng                         # noqa: E402

RIYADH = timezone(timedelta(hours=3))
REPO_SLUG = "omermasaad75-alt/-penny-stock-news-bot"
DEFAULT_PAGES = "https://omermasaad75-alt.github.io/-penny-stock-news-bot/"

STEP_SHORT = {
    "support_hold": "ثبات الدعم 3 جلسات",
    "impulse": "الانطلاقة ≈20%",
    "resistance": "أقرب مقاومة",
    "pullback": "العودة لاختبار الدعم",
    "sweep": "سحب السيولة/الاستعادة",
    "reclaim": "الاستعادة فوق الدعم",
}

CSV_COLUMNS = [
    ("symbol", "الرمز"), ("name", "الاسم"), ("phase", "المرحلة"),
    ("phase_ar", "وصف المرحلة"), ("score", "التقييم"), ("grade", "الدرجة"),
    ("last_close", "آخر إغلاق"), ("rs_date", "تاريخ التقسيم"), ("rs_ratio", "نسبة التقسيم"),
    ("impulse_pct", "الانطلاقة %"), ("resistance_level", "أقرب مقاومة"),
    ("resistance_distance_pct", "بعد المقاومة %"), ("sweep_low", "قاع سحب السيولة"),
    ("support_level", "الدعم الرئيسي"), ("support_hold_ok", "ثبات الدعم"),
    ("reclaim_sessions", "جلسات الاستعادة"), ("sequence_done", "خطوات مكتملة"),
    ("lifecycle_ready", "جاهز فنياً"), ("lifecycle_failed", "فشل"), ("missing_step", "الخطوة الناقصة"),
    ("trigger", "سعر التفعيل"), ("stop_structural", "وقف هيكلي"), ("rr_t2", "ربح/خطر"),
    ("last_close_time", "وقت آخر شمعة"),
    ("synthetic", "بيانات تجريبية"),
    ("price_source", "مصدر الأسعار"), ("split_source", "مصدر التقسيم"),
    ("fundamentals_source", "مصدر الأساسيات"), ("news_source", "مصدر الأخبار"),
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


def sequence_state(a: "eng.Analysis") -> tuple[int, str, str]:
    """(عدد الخطوات المكتملة، وصف الحالة، لون) — مطابق لمنطق الواجهة."""
    checks = a.lifecycle_checks or []
    if not checks:
        return 0, "لا تقسيم عكسي داخل النافذة", "muted"
    done = sum(1 for c in checks if c.get("ok"))
    if a.lifecycle_ready:
        return 6, "جاهز فنياً (6/6)", "ok"
    if a.lifecycle_failed:
        return done, "فشل — كسر الدعم دون استعادة", "bad"
    active = next((c for c in checks if not c.get("ok")), None)
    if active is None:
        return done, f"{done}/6", "warn"
    name = STEP_SHORT.get(str(active.get("key")), str(active.get("ar") or ""))
    return done, f"{done}/6 — ناقص: {name}", "warn"


def dots_html(a: "eng.Analysis") -> str:
    checks = a.lifecycle_checks or []
    if not checks:
        return '<span class="dots"><i class="dot muted"></i></span>'
    out = []
    for c in checks:
        cls = "ok" if c.get("ok") else ("active" if c.get("state") == "active" else "pending")
        title = html.escape(str(c.get("ar") or c.get("key") or ""))
        out.append(f'<i class="dot {cls}" title="{title}"></i>')
    return '<span class="dots">' + "".join(out) + "</span>"


def summarize(results: list) -> dict:
    with_rs = [a for a in results if a.rs_found]
    elig = [a for a in results if a.eligible]
    watch = [a for a in results if a.watchlist]
    ready = [a for a in results if a.lifecycle_ready]
    failed = [a for a in results if a.lifecycle_failed]
    return {
        "scanned": len(results),
        "with_reverse_split": len(with_rs),
        "eligible": len(elig),
        "watchlist": len(watch),
        "ready": len(ready),
        "failed": len(failed),
        "avg_score_watchlist": round(sum(a.score for a in watch) / len(watch), 1) if watch else 0,
        "top_symbol": results[0].symbol if results else "",
        "ready_symbols": [a.symbol for a in ready],
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
            a = eng.analyze_symbol(sym, hub, STRATEGY)
        except Exception as exc:                                   # noqa: BLE001
            a = eng.Analysis(symbol=sym)
            a.rejections.append(f"خطأ: {exc}")
        results.append(a)
        print(f"  [{i}/{len(syms)}] {sym:<8} مرحلة={a.phase} تقييم={a.score}", flush=True)
    results.sort(key=lambda x: (-x.phase, -x.score))
    sources = hub.sources_report()
    print(f"✔ انتهى المسح في {time.time() - t0:,.1f} ثانية — الوضع الفعّال: {sources['mode_effective']}",
          flush=True)
    return results, sources


# --------------------------------------------------------------------------- #
#  2) حفظ البيانات الخام
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
        "mode_setting": sources.get("mode_setting", mode),
        "summary": summary,
        "sources": sources,
        "rows": rows,
    }
    (site / "data" / "latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    # CSV بعناوين عربية مزدوجة (سهل الفتح في Excel)
    with (site / "data" / "latest.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow([ar for _, ar in CSV_COLUMNS])
        for a in results:
            lv = a.levels or {}
            done, _, _ = sequence_state(a)
            checks = a.lifecycle_checks or []
            missing = next((STEP_SHORT.get(str(c.get("key")), "") for c in checks if not c.get("ok")), "")
            look = {
                "symbol": a.symbol, "name": a.name, "phase": a.phase,
                "phase_ar": eng.phase_info(a.phase if (a.eligible or not a.rs_found) else 0)["ar"],
                "score": a.score, "grade": a.grade, "last_close": a.last_close,
                "rs_date": a.rs_date, "rs_ratio": a.rs_ratio_str,
                "impulse_pct": a.impulse_pct, "resistance_level": a.resistance_level,
                "resistance_distance_pct": a.resistance_distance_pct, "sweep_low": a.sweep_low,
                "support_level": a.support_level, "support_hold_ok": a.support_hold_ok,
                "reclaim_sessions": a.reclaim_sessions, "sequence_done": done,
                "lifecycle_ready": a.lifecycle_ready, "lifecycle_failed": a.lifecycle_failed,
                "missing_step": missing, "trigger": lv.get("trigger"),
                "stop_structural": lv.get("stop_structural"), "rr_t2": lv.get("rr_t2"),
                "last_close_time": a.as_of or "",
                "synthetic": "نعم" if a.synthetic else "لا",
                "price_source": a.price_source or "—",
                "split_source": a.split_source or "—",
                "fundamentals_source": a.fundamentals_source or "—",
                "news_source": a.news_source or "—",
            }
            w.writerow([look.get(key, "") for key, _ in CSV_COLUMNS])

    # سجل تاريخي مختصر (يفتح على الجوال بسرعة)
    hist_entry = {
        "stamp": stamp,
        "generated_at_riyadh": payload["generated_at_riyadh"],
        "mode": payload["mode"],
        "summary": summary,
        "top": [
            {"symbol": a.symbol, "phase": a.phase, "score": a.score, "grade": a.grade,
             "close": a.last_close, "impulse_pct": a.impulse_pct,
             "resistance_distance_pct": a.resistance_distance_pct,
             "ready": a.lifecycle_ready, "failed": a.lifecycle_failed,
             "sequence": sequence_state(a)[1]}
            for a in results[:15]
        ],
    }
    (site / "history" / f"{stamp}.json").write_text(
        json.dumps(hist_entry, ensure_ascii=False, indent=1), encoding="utf-8")

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
    picks: list[str] = []
    for a in results:
        if a.lifecycle_ready or a.watchlist or a.eligible:
            picks.append(a.symbol)
    if len(picks) < min(top_n, len(results)):
        picks += [a.symbol for a in results if a.symbol not in picks]
    targets = picks[:top_n]
    written = write_reports(targets, hub, out_dir, analyses={a.symbol: a for a in results})
    try:
        wl_md = watchlist_markdown(results, hub.sources_report())
        day = now_riyadh().strftime("%Y%m%d")
        (out_dir / f"watchlist_{day}.md").write_text(wl_md, encoding="utf-8")
        (out_dir / f"watchlist_{day}.html").write_text(
            markdown_to_html(wl_md, "قائمة الفرص — التقسيم العكسي"), encoding="utf-8")
    except Exception as exc:                                       # noqa: BLE001
        print(f"تعذّر توليد تقرير قائمة الفرص: {exc}", flush=True)
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
    # لقطة الهبوط تُشير إلى نفس اللوحة
    return out


# --------------------------------------------------------------------------- #
#  5) صفحة الهبوط
# --------------------------------------------------------------------------- #
LANDING_CSS = """
:root{
  --bg:#080b11; --bg2:#0d131c; --panel:#111826; --line:#1e2a3d; --txt:#e6edf7; --muted:#8b98ab;
  --green:#3fb950; --red:#f85149; --amber:#e3b341; --blue:#58a6ff; --purple:#a371f7;
}
*{box-sizing:border-box}
body{margin:0;background:linear-gradient(180deg,#070a0f,#0b1017 40%,#080b11);color:var(--txt);
  font-family:"Segoe UI","Noto Kufi Arabic",Tahoma,system-ui,-apple-system,sans-serif;line-height:1.6}
a{color:var(--blue);text-decoration:none}
.wrap{max-width:1180px;margin:0 auto;padding:18px 14px 60px}
header.top{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;
  background:rgba(13,19,28,.9);border:1px solid var(--line);border-radius:16px;padding:14px 16px;
  position:sticky;top:0;z-index:9;backdrop-filter:blur(8px)}
.brand{display:flex;gap:12px;align-items:center}
.logo{width:44px;height:44px;border-radius:12px;background:linear-gradient(135deg,#a371f7,#58a6ff);
  display:grid;place-items:center;font-weight:800;color:#05080d}
h1{font-size:18px;margin:0}
.sub{color:var(--muted);font-size:12px}
.badges{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.badge{font-size:12px;padding:5px 10px;border-radius:999px;border:1px solid var(--line);background:#0b111a}
.badge.live{border-color:#1f6f3d;color:#7ee787;background:rgba(63,185,80,.1)}
.badge.mixed{border-color:#8a6d1f;color:#ffd87a;background:rgba(227,179,65,.08)}
.badge.demo{border-color:#8a2828;color:#ff9999;background:rgba(248,81,73,.1)}
.row-badge{display:inline-block;font-size:11px;padding:2px 7px;border-radius:8px;margin-inline-start:4px}
.row-badge.ok{background:rgba(63,185,80,.15);color:#7ee787}
.row-badge.warn{background:rgba(227,179,65,.12);color:#e3b341}
.row-badge.bad{background:rgba(248,81,73,.15);color:#ff7b72}
.cta{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0 6px}
.btn{display:inline-flex;gap:8px;align-items:center;padding:12px 18px;border-radius:12px;font-weight:700;
  border:1px solid var(--line);background:var(--panel);color:var(--txt);font-size:14px}
.btn.primary{background:linear-gradient(135deg,#2ea043,#3fb950);border-color:#2ea043;color:#04170a}
.btn.purple{background:linear-gradient(135deg,#7c5cd6,#a371f7);border-color:#7c5cd6;color:#0b0616}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:18px 0}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:12px 14px}
.kpi span{display:block;color:var(--muted);font-size:12px}
.kpi b{font-size:22px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:14px 16px;margin:16px 0}
.card h2{font-size:15px;margin:0 0 10px;display:flex;gap:8px;align-items:center;justify-content:space-between}
.tblwrap{overflow-x:auto;-webkit-overflow-scrolling:touch;border-radius:12px}
table{width:100%;border-collapse:collapse;font-size:13px;min-width:760px}
th,td{padding:9px 8px;text-align:right;border-bottom:1px solid #16202f;white-space:nowrap}
th{background:#0b111a;color:var(--muted);font-weight:600;position:sticky;top:0}
tr:hover td{background:#0e1520}
.sym{font-weight:800;color:#fff}
.dots{display:inline-flex;gap:3px;margin-inline-end:6px;vertical-align:middle}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block;background:#2b3646}
.dot.ok{background:var(--green)}
.dot.active{background:var(--amber);box-shadow:0 0 0 2px rgba(227,179,65,.25)}
.dot.pending{background:#39465a}
.dot.muted{background:#4a5568}
.c-green{color:#7ee787}.c-amber{color:#e3b341}.c-red{color:#ff7b72}.muted{color:var(--muted)}
.grade{font-weight:800;padding:2px 8px;border-radius:8px;background:#0d1620;border:1px solid var(--line)}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}
ul.files{list-style:none;margin:0;padding:0}
ul.files li{display:flex;justify-content:space-between;gap:10px;padding:7px 0;border-bottom:1px dashed #16202f;font-size:13px}
footer{color:var(--muted);font-size:12px;margin-top:26px;border-top:1px solid var(--line);padding-top:14px}
.note{background:#0d1620;border:1px dashed #26364d;border-radius:12px;padding:10px 12px;font-size:12.5px;color:#b9c6d8}
.mcards{display:none}
.mcard{background:#0b111a;border:1px solid var(--line);border-radius:14px;padding:11px 13px;margin-bottom:10px}
.mcard-head{display:flex;justify-content:space-between;align-items:center;gap:8px}
.mcard-seq{margin-top:7px;font-size:11.5px}
.mcard-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-top:9px}
.mcard-grid div{background:#0e1520;border:1px solid #16202f;border-radius:10px;padding:6px 8px;font-size:12.5px}
.mcard-grid span{display:block;color:var(--muted);font-size:10.5px;margin-bottom:2px}
.mcard-actions{display:flex;gap:8px;margin-top:10px}
.mcard-actions .btn{flex:1;justify-content:center;padding:9px 10px;font-size:12.5px;border-radius:10px}
@media(max-width:760px){
  .wrap{padding:12px 10px 48px}
  header.top{position:static;display:block;padding:12px 13px}
  .brand{align-items:flex-start;gap:10px}
  .brand>div{min-width:0}
  .logo{width:38px;height:38px;border-radius:11px;font-size:13px}
  h1{font-size:15px;line-height:1.45;margin:0 0 3px;overflow-wrap:anywhere}
  .sub{font-size:11px;line-height:1.5;overflow-wrap:anywhere;display:block}
  .badges{margin-top:10px;gap:6px}
  .badge{font-size:11px;padding:4px 9px}
  .kpi b{font-size:19px}
  .btn{width:100%;justify-content:center}
  .cta{display:grid;grid-template-columns:1fr 1fr;gap:8px}
  .cta .btn.primary{grid-column:1 / -1}
  .tblwrap{display:none}
  .mcards{display:block}
  .card{padding:12px 12px;border-radius:14px}
  .card h2{font-size:14px;flex-wrap:wrap;gap:4px}
  ul.files li{font-size:12.5px;flex-wrap:wrap}
}
"""

LANDING = Template("""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#080b11">
<meta http-equiv="cache-control" content="no-cache, must-revalidate">
<title>لوحة رصد التقسيم العكسي — آخر تحديث $updated</title>
<link rel="icon" type="image/png" sizes="32x32" href="favicon.png">
<link rel="icon" type="image/png" sizes="192x192" href="icons/icon-192.png">
<link rel="apple-touch-icon" href="icons/apple-touch-icon.png">
<link rel="manifest" href="manifest.json">
<style>$css</style>
</head>
<body>
<div class="wrap">
<header class="top">
  <div class="brand">
    <div class="logo">RS</div>
    <div>
      <h1>منصة رصد التقسيم العكسي والانعكاس</h1>
      <div class="sub">Reverse-Split Oversold Reversal · فريم اليومي و4 ساعات</div>
    </div>
  </div>
  <div class="badges">
    <span class="badge $mode_class">وضع البيانات: $mode_label</span>
    <span class="badge">آخر مسح: $updated</span>
  </div>
</header>

$warning_banner

<div class="cta">
  <a class="btn primary" href="dashboard.html?v=$stamp">افتح اللوحة التفاعلية</a>
  <a class="btn purple" href="data/latest.csv">تنزيل الجدول (CSV)</a>
  <a class="btn" href="data/latest.json">البيانات (JSON)</a>
  <a class="btn" href="$actions_url">سجل عمليات المسح</a>
</div>
<div class="note">اللوحة ملف واحد يعمل داخل جوالك بلا إنترنت بعد فتحه أول مرة — يمكنك حفظه في الشاشة الرئيسية
(من قائمة المتصفح: «إضافة إلى الشاشة الرئيسية»).</div>

<section class="kpis">
  <div class="kpi"><span>رموز مفحوصة</span><b>$scanned</b></div>
  <div class="kpi"><span>تقسيم عكسي داخل النافذة</span><b class="c-green">$with_rs</b></div>
  <div class="kpi"><span>فرص مؤهلة</span><b class="c-green">$eligible</b></div>
  <div class="kpi"><span>قائمة المتابعة</span><b class="c-amber">$watchlist</b></div>
  <div class="kpi"><span>جاهز فنياً (6/6)</span><b class="c-green">$ready</b></div>
  <div class="kpi"><span>متوسط تقييم المتابعة</span><b>$avg</b></div>
</section>

<div class="card">
  <h2><span>أفضل الفرص الآن</span><span class="muted">$rowcount رمز — مرتّبة بالمرحلة ثم التقييم</span></h2>
  <div class="tblwrap">
  <table>
    <thead><tr>
      <th>الرمز</th><th>تسلسل دورة الحياة</th><th>المرحلة</th><th>التقييم</th><th>آخر إغلاق</th>
      <th>التقسيم</th><th>الانطلاقة</th><th>أقرب مقاومة</th><th>سحب السيولة</th><th>الدعم</th><th>التقرير</th>
    </tr></thead>
    <tbody>
$rows_html
    </tbody>
  </table>
  </div>
  <div class="mcards">
$cards_html
  </div>
</div>

<div class="grid2">
  <div class="card">
    <h2><span>تقارير التحليل</span><span class="muted">$report_count تقرير</span></h2>
    <ul class="files">$reports_html</ul>
  </div>
  <div class="card">
    <h2><span>آخر عمليات المسح</span><span class="muted">سجل زمني</span></h2>
    <ul class="files">$history_html</ul>
  </div>
</div>

<footer>
  <div>مواعيد المسح التلقائي: ٣ مرات كل يوم عمل (افتتاح السوق · منتصف الجلسة · بعد الإغلاق).</div>
  <div>المصدر: <a href="$repo_url">$repo_slug</a> · المزوّدون: $providers</div>
  <div>$disclaimer</div>
</footer>
</div>
</body>
</html>
""")


def _rows_html(results: list, report_names: dict, stamp: str) -> str:
    out = []
    for a in results[:60]:
        lv = a.levels or {}
        done, label, color = sequence_state(a)
        cls = {"ok": "c-green", "warn": "c-amber", "bad": "c-red", "muted": "muted"}[color]
        phase_ar = eng.phase_info(a.phase if (a.eligible or not a.rs_found) else 0)["ar"]
        phase_ar = phase_ar.split("—")[-1].strip() if "—" in phase_ar else phase_ar
        rep = report_names.get(a.symbol)
        rep_cell = f'<a href="reports/{rep}">تحليل ›</a>' if rep else '<span class="muted">—</span>'
        # شارة جودة البيانات للصف
        if getattr(a, 'synthetic', False):
            rb = '<span class="row-badge bad">🔴 تجريبي</span>'
        elif getattr(a, 'price_source', '') and not getattr(a, 'split_source', ''):
            rb = '<span class="row-badge warn">🟠 ناقص</span>'
        elif getattr(a, 'price_source', ''):
            rb = '<span class="row-badge ok">🟢 حقيقي</span>'
        else:
            rb = '<span class="row-badge warn">🟠 ناقص</span>'
        out.append(
            "<tr>"
            f'<td class="sym">{html.escape(a.symbol)}{rb}'
            f'<div class="muted" style="font-weight:400;font-size:11px">'
            f'{html.escape((a.name or "")[:22])}</div></td>'
            f"<td>{dots_html(a)}<div class=\"{cls}\" style=\"font-size:11.5px\">{html.escape(label)}</div></td>"
            f'<td>{html.escape(phase_ar[:26])}</td>'
            f'<td><span class="grade">{a.score} {a.grade}</span></td>'
            f"<td>{_f(a.last_close, 3)}</td>"
            f"<td>{html.escape(a.rs_ratio_str or '—')}</td>"
            f"<td>{_pct(a.impulse_pct)}</td>"
            f"<td>{_f(a.resistance_level, 3)} <span class=\"muted\">{_pct(a.resistance_distance_pct)}</span></td>"
            f"<td>{_f(a.sweep_low, 3)}</td>"
            f"<td>{_f(a.support_level, 3)}</td>"
            f"<td>{rep_cell}</td>"
            "</tr>")
    if not out:
        out.append('<tr><td colspan="11" class="muted">لا توجد نتائج — أعد تشغيل المسح.</td></tr>')
    return "\n".join(out)


def _cards_html(results: list, report_names: dict, stamp: str) -> str:
    """بطاقات الجوال (بديل الجدول العريض على الشاشات الصغيرة)."""
    out = []
    for a in results[:40]:
        done, label, color = sequence_state(a)
        cls = {"ok": "c-green", "warn": "c-amber", "bad": "c-red", "muted": "muted"}[color]
        rep = report_names.get(a.symbol)
        links = []
        if rep:
            links.append(f'<a class="btn" href="reports/{rep}">تحليل مفصل</a>')
        links.append(f'<a class="btn" href="dashboard.html?v={stamp}">على اللوحة</a>')
        out.append(
            '<div class="mcard">'
            '<div class="mcard-head">'
            f'<div><span class="sym">{html.escape(a.symbol)}</span> '
            f'<span class="muted" style="font-size:11px">{html.escape((a.name or "")[:20])}</span></div>'
            f'<span class="grade">{a.score} {a.grade}</span></div>'
            f'<div class="mcard-seq">{dots_html(a)}<span class="{cls}">{html.escape(label)}</span></div>'
            '<div class="mcard-grid">'
            f'<div><span>آخر إغلاق</span>{_f(a.last_close, 3)}</div>'
            f'<div><span>الانطلاقة</span>{_pct(a.impulse_pct)}</div>'
            f'<div><span>أقرب مقاومة</span>{_f(a.resistance_level, 3)}</div>'
            f'<div><span>سحب السيولة</span>{_f(a.sweep_low, 3)}</div>'
            f'<div><span>الدعم الرئيسي</span>{_f(a.support_level, 3)}</div>'
            f'<div><span>نسبة التقسيم</span>{html.escape(a.rs_ratio_str or "—")}</div>'
            '</div>'
            f'<div class="mcard-actions">{"".join(links)}</div>'
            '</div>')
    if not out:
        out.append('<div class="mcard muted">لا توجد نتائج — أعد تشغيل المسح.</div>')
    return "\n".join(out)


def build_landing(site: Path, payload: dict, written: list[dict]) -> Path:
    summary = payload["summary"]
    mode = payload.get("mode", "demo")
    rows_data = payload.get("rows") or []
    # حساب عدد الصفوف التجريبية والحقيقية
    synth_count = sum(1 for r in rows_data if r.get("synthetic"))
    real_count = len(rows_data) - synth_count
    all_real = (mode == "live" and synth_count == 0 and real_count > 0)
    is_mixed = mode == "live" and synth_count > 0
    report_names = {}
    for w in written:
        name = Path(w.get("html") or "").name
        if name:
            report_names[w["symbol"]] = name
    reports_sorted = sorted(written, key=lambda w: (-w.get("score", 0), w.get("symbol", "")))
    reports_html = "\n".join(
        f'<li><span class="sym">{html.escape(w["symbol"])}</span>'
        f'<span class="muted">مرحلة {w.get("phase")} · {w.get("score")}</span>'
        f'<span><a href="reports/{Path(w["html"]).name}">HTML</a> · '
        f'<a href="reports/{Path(w["md"]).name}">MD</a></span></li>'
        for w in reports_sorted) or '<li class="muted">لا تقارير</li>'
    history_html = "\n".join(
        f'<li><span>{html.escape(str(h.get("generated_at_riyadh", "")))}</span>'
        f'<span class="muted">{h.get("summary", {}).get("scanned", 0)} رمز · جاهز '
        f'{h.get("summary", {}).get("ready", 0)}</span>'
        f'<span><a href="history/{h.get("stamp")}.json">تفاصيل</a></span></li>'
        for h in (payload.get("history") or [])) or '<li class="muted">لا سجل بعد</li>'
    providers = " · ".join(
        f'{p.get("name")} ({p.get("source_kind")}/{p.get("ok")})'
        for p in (payload.get("sources", {}) or {}).get("providers", [])[:6]) or "—"
    live = mode == "live"
    # شارة الوضع
    if all_real:
        mode_class = "live"
        mode_label = "حقيقية 🟢 — Yahoo/SEC/FINRA"
        warning_banner = ""
    elif is_mixed:
        mode_class = "mixed"
        mode_label = f"مختلطة 🟠 — {real_count} حقيقي / {synth_count} تجريبي"
        warning_banner = (
            '<div class="warn" style="background:#3a2a08;border:1px solid #8a6d1f;'
            'border-radius:12px;padding:12px 14px;margin:14px 0;color:#ffd87a;font-size:13px;">'
            '⚠ <b>تحذير:</b> يوجد صفوف ببيانات تجريبية في هذه النشرة — لا تعتمد عليها في التداول. '
            'الوضع الحي مُفعّل لكن بعض المزوّدين فشلوا.</div>'
        )
    else:
        mode_class = "demo"
        mode_label = "تجريبية 🔴 — بيانات محاكاة (محظورة في النشر)"
        warning_banner = (
            '<div class="warn" style="background:#3d0f0f;border:1px solid #8a2828;'
            'border-radius:12px;padding:12px 14px;margin:14px 0;color:#ff9999;font-size:13px;">'
            '🔴 <b>هذه النشرة تحتوي بيانات تجريبية بالكامل.</b> '
            'هذه البيانات غير مسموح بها في مسار النشر الإنتاجي.</div>'
        )
    ctx = {
        "css": LANDING_CSS,
        "updated": html.escape(payload["generated_at_riyadh"]),
        "stamp": payload["stamp"],
        "mode_class": mode_class,
        "mode_label": mode_label,
        "warning_banner": warning_banner,
        "scanned": summary["scanned"], "with_rs": summary["with_reverse_split"],
        "eligible": summary["eligible"], "watchlist": summary["watchlist"],
        "ready": summary["ready"], "avg": summary["avg_score_watchlist"],
        "rowcount": min(len(payload.get("rows") or []), 60),
        "rows_html": _rows_html([_RowProxy(r) for r in (payload.get("rows") or [])], report_names,
                                payload["stamp"]),
        "cards_html": _cards_html([_RowProxy(r) for r in (payload.get("rows") or [])], report_names,
                                  payload["stamp"]),
        "reports_html": reports_html, "report_count": len(written),
        "history_html": history_html,
        "repo_slug": REPO_SLUG,
        "repo_url": f"https://github.com/{REPO_SLUG}",
        "actions_url": f"https://github.com/{REPO_SLUG}/actions/workflows/daily-scan.yml",
        "providers": html.escape(providers),
        "disclaimer": ("تنبيه: هذا النظام أداة تحليل فني وليست نصيحة مالية — القرار النهائي للمستثمر."),
    }
    out = site / "index.html"
    out.write_text(LANDING.substitute(ctx), encoding="utf-8")
    print(f"✔ صفحة الهبوط: {out}", flush=True)
    return out


class _RowProxy:
    """طبقة رقيقة تجعل صفوف JSON قابلة للاستخدام كما لو كانت كائنات تحليل."""

    def __init__(self, row: dict):
        self._r = row or {}

    def __getattr__(self, item):
        return self._r.get(item)

    @property
    def levels(self) -> dict:
        return self._r.get("levels") or {}

    @property
    def lifecycle_checks(self) -> list:
        return self._r.get("lifecycle_checks") or []


def write_manifest(site: Path, stamp: str) -> None:
    import shutil
    icons_dir = site / "icons"
    icons_dir.mkdir(parents=True, exist_ok=True)
    # نسخ الأيقونات من web/icons إلى site/icons
    src_icons = ROOT / "web" / "icons"
    if src_icons.exists():
        for icon in src_icons.glob("*.png"):
            shutil.copy2(icon, icons_dir / icon.name)
    (site / "manifest.json").write_text(json.dumps({
        "name": "منصة رصد التقسيم العكسي والانعكاس",
        "short_name": "RS Reversal",
        "description": "رصد أسهم التقسيم العكسي — بيانات حقيقية من Yahoo/SEC/FINRA",
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
    # نسخ favicon إلى جذر الموقع
    favicon_src = src_icons / "favicon-32.png" if src_icons.exists() else None
    if favicon_src and favicon_src.exists():
        shutil.copy2(favicon_src, site / "favicon.png")


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="مسح يومي + بناء موقع اللوحة")
    ap.add_argument("--site", default="site", help="مجلد الموقع الناتج (يُنشر على GitHub Pages)")
    ap.add_argument("--limit", type=int, default=40, help="عدد الرموز")
    ap.add_argument("--mode", default=None, help="demo | live | auto (افتراضي: من الإعدادات)")
    ap.add_argument("--symbols", default="", help="رموز محددة مفصولة بفواصل (تجاوز الكون)")
    ap.add_argument("--top-reports", type=int, default=12, help="عدد التقارير المولّدة")
    ap.add_argument("--dashboard-symbols", type=int, default=40, help="عدد الرموز داخل اللوحة الثابتة")
    ap.add_argument("--no-dashboard", action="store_true", help="تخطّي بناء اللوحة الثقيلة (اختبار سريع)")
    args = ap.parse_args()

    started = now_riyadh()
    site = Path(args.site)
    if not site.is_absolute():
        site = ROOT / site
    site.mkdir(parents=True, exist_ok=True)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or None
    # منع نشر بيانات تجريبية: في وضع live/auto نرفض أي synthetic > 0 أو effective != live
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
        # صرامة النشر: وضع live يجب أن يكون فعّالاً فعلاً
        if mode_eff != "live":
            print(f"✖ فشل: وضع البيانات الفعّال هو '{mode_eff}' وليس 'live' — تعذّر الوصول لمزوّدي "
                  f"البيانات الحقيقية. لن تُنشر بيانات تجريبية.", flush=True)
            return 3
        # في الوضع الحي لا يُسمح بصفوف synthetic
        if synth_rows > 0:
            print(f"✖ فشل: وُجد {synth_rows} صف ببيانات تجريبية في الوضع الحي — "
                  f"هذا تسريب غير مسموح به.", flush=True)
            return 4
        print(f"✔ تحقق الوضع: كل البيانات حقيقية (mode_effective=live، synthetic=0).", flush=True)

    if not results:
        print("✖ لا نتائج — تحقّق من الكون أو الاتصال.", flush=True)
        return 2
    # في وضع live قد تكون النتائج كلها مستبعدة (لا تقسيمات عكسية) — هذا طبيعي ومقبول
    real_with_data = sum(1 for a in results if len(getattr(a, 'rejections', [])) == 0)
    print(f"▶ رموز حقيقية مع بيانات صالحة: {real_with_data} / {len(results)}", flush=True)

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
