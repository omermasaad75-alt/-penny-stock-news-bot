"""واجهة سطر الأوامر — مسح السوق وتوليد التقارير بدون واجهة رسومية.

أمثلة:
    python -m app.scanner --scan
    python -m app.scanner --scan --mode demo --limit 40
    python -m app.scanner --symbols JZ,NVEX --report
    python -m app.scanner --scan --json --out reports/output/scan.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from .config import REPORTS_DIR, settings
from .providers.hub import DataHub, get_hub
from .reporting import watchlist_markdown, write_reports
from .screener import (
    ScreenConfigError,
    build_params,
    load_overrides,
    parse_weights,
    rank_all,
    rank_eligible,
    rules_snapshot,
    write_scan_csv,
)
from .strategy import engine as eng


def _f(x, d=2):
    if x is None:
        return "—"
    try:
        return f"{float(x):,.{d}f}"
    except (TypeError, ValueError):
        return str(x)


def _pct(x, d=1):
    if x is None:
        return "—"
    return f"{float(x):+,.{d}f}%"


def _m(x):
    if x is None:
        return "—"
    v = float(x)
    if v >= 1e9:
        return f"{v/1e9:.2f}B"
    if v >= 1e6:
        return f"{v/1e6:.2f}M"
    if v >= 1e3:
        return f"{v/1e3:.1f}K"
    return f"{v:.0f}"


def print_table(results: list[eng.Analysis], only_watch: bool = False) -> None:
    rows = [a for a in results if a.watchlist] if only_watch else [a for a in results if a.eligible]
    print()
    print("=" * 150)
    print(f"{'الرمز':<7}{'المرحلة':<38}{'التقييم':<9}{'السعر':<9}{'التقسيم':<14}"
          f"{'صعود':<8}{'هبوط':<8}{'ثبات':<6}{'RSI':<10}{'الفلوت':<9}{'أسهم مكشوفة':<9}{'اختراق':<9}{'ر/ع':<7}")
    print("=" * 150)
    for a in rows:
        lv = a.levels or {}
        phase = eng.phase_info(a.phase if (a.eligible or not a.rs_found) else 0)["ar"]
        phase = phase.split("—")[-1].strip() if "—" in phase else phase
        if a.gam_candidate:
            phase = "GAM — غير مؤهل (مسار ثانوي)"
        print(f"{a.symbol:<7}{phase[:36]:<38}{str(a.score)+' '+a.grade:<9}"
              f"{_f(a.last_close, 3):<9}{(a.rs_ratio_str or '—'):<14}{_pct(a.spike_pct):<8}"
              f"{_pct(a.decline_pct):<8}{str(a.base_sessions):<6}"
              f"{(_f(a.rsi_trough,0)+'→'+_f(a.rsi_now,0)):<10}{_m(a.float_shares):<9}"
              f"{_f(a.short_shares,0):<9}"
              f"{_f(lv.get('trigger'),3):<9}{_f(lv.get('rr_t2'),2):<7}")
        if a.rejections:
            print(f"        ↳ مستبعد: {' | '.join(a.rejections)[:130]}")
    print("=" * 150)


def print_symbol_report(a: eng.Analysis) -> None:
    info = eng.phase_info(a.phase if (a.eligible or not a.rs_found) else 0)
    print()
    print("┌" + "─" * 96)
    status = "مرشح GAM — غير مؤهل/ليس توصية" if a.gam_candidate else info["ar"]
    print(f"│ {a.symbol} — {a.name}  |  {status}")
    print(f"│ التقييم: {a.score}/100 ({a.grade})  |  مصدر البيانات: {a.data_source}  |  آخر جلسة: {a.as_of}")
    if a.gam_candidate:
        print("│ GAM مسار ثانوي فقط؛ RSI من بيانات المسح الحالي ولا توجد حالة رفض تاريخية محفوظة.")
    print("├" + "─" * 96)
    for w in a.why_ar:
        print(f"│ • {w[:150]}")
    print(f"│ ▶ {a.action_ar[:190]}")
    if a.rejections:
        for r in a.rejections:
            print(f"│ ✖ {r}")
    lv = a.levels or {}
    t = lv.get("targets", {})
    print(f"│ دخول {_f(lv.get('trigger'),3)} | وقف {_f(lv.get('stop'),3)} ({_pct(lv.get('stop_pct'))}) "
          f"| أهداف {_f(t.get('t1_382'),3)} / {_f(t.get('t2_618'),3)} / {_f(t.get('t3_full'),3)} "
          f"| ر/ع {_f(lv.get('rr_t2'),2)}:1")
    if a.patterns_daily:
        print("│ نماذج: " + " · ".join(p["name_ar"] for p in a.patterns_daily[:4]))
    print("└" + "─" * 96)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="منصة رصد التقسيم العكسي والانعكاس — سطر الأوامر")
    ap.add_argument("--scan", action="store_true", help="مسح كل الرموز في الكون")
    ap.add_argument("--symbols", type=str, default="", help="رموز محددة مفصولة بفاصلة")
    ap.add_argument("--mode", type=str, default=None, choices=["demo", "live", "auto"],
                    help="وضع البيانات")
    ap.add_argument("--limit", type=int, default=60, help="أقصى عدد رموز")
    ap.add_argument("--only-watch", action="store_true", help="عرض قائمة المتابعة فقط")
    ap.add_argument("--report", action="store_true", help="توليد تقارير Markdown/HTML/JSON")
    ap.add_argument("--out", type=str, default="", help="مسار ملف JSON للناتج")
    ap.add_argument("--json", action="store_true", help="طباعة الناتج كـ JSON")
    # تعديلات مرنة للفلاتر الخمسة فقط (لا تضيف شرطاً سادساً).
    ap.add_argument("--config", type=str, default="",
                    help="ملف JSON لمعاملات StrategyParams (قسم core/scoring أو مسطح)")
    ap.add_argument("--min-price", type=float, default=None, help="أدنى سعر (افتراضي 1.0$)")
    ap.add_argument("--max-price", type=float, default=None, help="أعلى سعر (افتراضي 5.0$)")
    ap.add_argument("--rs-window-days", type=int, default=None,
                    help="نافذة التقسيم العكسي بالأيام (افتراضي 60)")
    ap.add_argument("--min-drop-pct", type=float, default=None,
                    help="أدنى هبوط من قمة ما بعد التقسيم (افتراضي 30%%)")
    ap.add_argument("--rsi-oversold", type=float, default=None,
                    help="حد التشبع البيعي RSI (افتراضي 30)")
    ap.add_argument("--max-short-shares", type=float, default=None,
                    help="سقف الأسهم المكشوفة الحصري (افتراضي 20,000)")
    ap.add_argument("--weights", type=str, default="",
                    help="أوزان النقاط: volume=25,patterns=35,support=25,ema=15")
    ap.add_argument("--csv", type=str, default="", help="مسار CSV للجدول الرئيسي (المؤهلون فقط)")
    ap.add_argument("--csv-all", action="store_true",
                    help="مع --csv: تصدير كل الصفوف (للتشخيص) مع عمود الأهلية")
    args = ap.parse_args(argv)

    def status(*values, **kwargs):
        print(*values, file=sys.stderr if args.json else sys.stdout, **kwargs)

    # بناء معاملات الجلسة: الملف أولاً ثم أعلام CLI تتغلب عليه.
    overrides: dict = {}
    try:
        if args.config:
            overrides.update(load_overrides(args.config))
        for key, value in (
            ("min_price", args.min_price), ("max_price", args.max_price),
            ("rs_lookback_days", args.rs_window_days),
            ("min_drop_pct", args.min_drop_pct),
            ("rsi_oversold", args.rsi_oversold),
            ("max_short_shares", args.max_short_shares),
        ):
            if value is not None:
                overrides[key] = value
        if args.weights:
            overrides["weights"] = parse_weights(args.weights)
        params = build_params(overrides)
    except ScreenConfigError as exc:
        print(f"✖ إعداد فحص غير صالح: {exc}", file=sys.stderr)
        return 2

    hub: DataHub = get_hub(mode=args.mode, refresh=bool(args.mode))
    src = hub.sources_report()
    status(f"▶ {settings.app_name_ar} v{settings.version}")
    status(f"▶ وضع البيانات الفعّال: {src['mode_effective']} (المُعدّ: {src['mode_setting']})")
    status(
        "▶ فلاتر الأهلية الخمسة: "
        f"{params.min_price:g}–{params.max_price:g}$ · "
        f"تقسيم ≤{params.rs_lookback_days}ي · "
        f"هبوط ≥{params.min_drop_pct:g}% · "
        f"RSI<{params.rsi_oversold:g} · "
        f"Short<{params.max_short_shares:,.0f}"
    )

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] if args.symbols else []
    if not syms:
        syms = hub.universe(limit=args.limit, params=params)

    if not (args.scan or args.symbols):
        status("استخدم --scan لمسح الكون أو --symbols لتحديد رموز. مثال: python -m app.scanner --scan")
        return 0

    status(f"▶ عدد الرموز: {len(syms)} — بدء التحليل…")
    results: list[eng.Analysis] = []
    for i, s in enumerate(syms, 1):
        try:
            a = eng.analyze_symbol(s, hub, params)
        except Exception as exc:  # noqa: BLE001
            a = eng.Analysis(symbol=s)
            eng.add_rejection(a, "analysis_error", f"خطأ: {exc}")
        results.append(a)
        status(f"\r  [{i}/{len(syms)}] {s:<8}", end="", flush=True)
    status("\r" + " " * 40 + "\r", end="")

    eligible_rows = rank_eligible(results)
    ordered = rank_all(results)

    payload = {
        "mode": src["mode_effective"], "generated_at": datetime.now().isoformat(timespec="seconds"),
        "rules": rules_snapshot(params),
        "summary": {
            "scanned": len(results),
            "with_reverse_split": sum(1 for a in results if a.rs_found),
            "watchlist": sum(1 for a in results if a.watchlist),
            "eligible": len(eligible_rows),
            "gam_candidates": sum(1 for a in results if a.gam_candidate),
        },
        "gam_policy": {
            "secondary_lane_only": True,
            "eligible": False,
            "price_range": [params.min_price, params.max_price],
            "allowed_failures": ["decline", "rsi"],
            "rsi_scope": eng.GAM_RSI_SCOPE,
            "historical_state_persisted": False,
        },
        "rows": [a.to_dict() for a in eligible_rows],
        "rejected": [a.to_dict() for a in ordered if not a.eligible],
    }
    if args.json:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(text, encoding="utf-8")
            status(f"✔ تم كتابة JSON إلى {args.out}")
        else:
            print(text)
    else:
        print_table(ordered, only_watch=args.only_watch)
        for a in ordered:
            if a.watchlist or a.gam_candidate:
                print_symbol_report(a)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
            status(f"✔ تم كتابة JSON إلى {args.out}")

    if args.csv:
        count = write_scan_csv(args.csv, results, include_rejected=args.csv_all)
        scope = "كل الصفوف (تشخيص)" if args.csv_all else "المؤهلون فقط (الجدول الرئيسي)"
        status(f"✔ CSV: {count} صفاً — {scope} — {args.csv}")

    if args.report:
        targets = [a.symbol for a in results if a.rs_found] or syms
        written = write_reports(targets, hub, REPORTS_DIR, analyses={a.symbol: a for a in results})
        wl = watchlist_markdown(results, src)
        stamp = datetime.now().strftime("%Y%m%d")
        (REPORTS_DIR / f"watchlist_{stamp}.md").write_text(wl, encoding="utf-8")
        status(f"✔ تم توليد {len(written)} تقريراً في {REPORTS_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
