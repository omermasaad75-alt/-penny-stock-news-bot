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
from .strategy import engine as eng


def _f(x, d=2):
    if x is None:
        return "—"
    try:
        return f"{float(x):,.{d}f}"
    except (TypeError, ValueError):
        return str(x)


def print_table(results: list[eng.Analysis], only_watch: bool = False) -> None:
    rows = [a for a in results if a.watchlist] if only_watch else results
    print()
    print("=" * 130)
    print(f"{'الرمز':<7}{'الحالة':<32}{'الدرجة':<10}{'السعر':<9}{'القمة÷2':<10}"
          f"{'قاع2':<9}{'دخول':<9}{'وقف':<9}{'هدف 25%':<10}{'قمة الصعود':<8}")
    print("=" * 130)
    for a in rows:
        lv = a.levels or {}
        dz = (a.drop_zone or {}).get("zone")
        pat = a.pattern or {}
        print(f"{a.symbol:<7}{a.phase_ar[:30]:<32}{str(a.score)+' '+a.grade:<10}"
              f"{_f(a.last_close, 3):<9}{_f(dz, 2):<10}{_f(pat.get('low2'), 3):<9}"
              f"{_f(lv.get('trigger'), 3):<9}{_f(lv.get('stop'), 3):<9}"
              f"{_f((lv.get('targets') or {}).get('t1'), 3):<10}"
              f"{_f((a.drop_zone or {}).get('spike_high'), 2):<8}")
        if a.rejections:
            print(f"        ↳ مستبعد: {' | '.join(a.rejections)[:130]}")
    print("=" * 130)


def print_symbol_report(a: eng.Analysis) -> None:
    print()
    print("┌" + "─" * 96)
    print(f"│ {a.symbol} — {a.name}  |  {a.phase_ar}")
    print(f"│ التقييم: {a.score}/100 ({a.grade})  |  مصدر البيانات: {a.data_source}  |  آخر جلسة: {a.as_of}")
    print("├" + "─" * 96)
    for item in a.breakdown:
        print(f"│   {item['ar']:<36} {item['points']:>5.1f} / {item['weight']:<4.0f}")
    print("├" + "─" * 96)
    for w in a.why_ar:
        print(f"│ • {w[:150]}")
    print(f"│ ▶ {a.action_ar[:190]}")
    for r in a.rejections:
        print(f"│ ✖ {r}")
    plan = a.trade_plan or {}
    if plan.get("valid"):
        t = plan.get("targets", [])
        print(f"│ دخول {plan['entry']:.3f} | وقف {plan['stop']:.3f} | أهداف "
              + " / ".join(f"{x:.3f}" for x in t)
              + f" | حجم المركز {plan['position_size']:,} سهم")
    if a.patterns_daily:
        print("│ نماذج: " + " · ".join(p["name_ar"] for p in a.patterns_daily[:4]))
    print("└" + "─" * 96)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ماسح الاستراتيجية — سطر الأوامر")
    ap.add_argument("--scan", action="store_true", help="مسح كل الرموز في الكون")
    ap.add_argument("--symbols", type=str, default="", help="رموز محددة مفصولة بفاصلة")
    ap.add_argument("--mode", type=str, default=None, choices=["demo", "live", "auto"],
                    help="وضع البيانات")
    ap.add_argument("--limit", type=int, default=60, help="أقصى عدد رموز")
    ap.add_argument("--only-watch", action="store_true", help="عرض قائمة المتابعة فقط")
    ap.add_argument("--report", action="store_true", help="توليد تقارير Markdown/HTML/JSON")
    ap.add_argument("--out", type=str, default="", help="مسار ملف JSON للناتج")
    ap.add_argument("--json", action="store_true", help="طباعة الناتج كـ JSON")
    args = ap.parse_args(argv)

    hub: DataHub = get_hub(mode=args.mode, refresh=bool(args.mode))
    src = hub.sources_report()
    print(f"▶ {settings.app_name_ar} v{settings.version}")
    print(f"▶ وضع البيانات الفعّال: {src['mode_effective']} (المُعدّ: {src['mode_setting']})")

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] if args.symbols else []
    if not syms:
        syms = hub.universe(limit=args.limit)

    if not (args.scan or args.symbols):
        print("استخدم --scan لمسح الكون أو --symbols لتحديد رموز. مثال: python -m app.scanner --scan")
        return 0

    print(f"▶ عدد الرموز: {len(syms)} — بدء التحليل…")
    results: list[eng.Analysis] = []
    for i, s in enumerate(syms, 1):
        try:
            a = eng.analyze_symbol(s, hub)
        except Exception as exc:  # noqa: BLE001
            a = eng.Analysis(symbol=s)
            eng.add_rejection(a, "analysis_error", f"خطأ: {exc}")
        results.append(a)
        print(f"\r  [{i}/{len(syms)}] {s:<8}", end="", flush=True)
    print("\r" + " " * 40 + "\r", end="")

    results.sort(key=lambda x: (-x.phase, -x.score))

    if args.json:
        payload = {
            "mode": src["mode_effective"], "generated_at": datetime.now().isoformat(timespec="seconds"),
            "summary": {
                "scanned": len(results),
                "watchlist": sum(1 for a in results if a.watchlist),
                "eligible": sum(1 for a in results if a.eligible),
                "ready": sum(1 for a in results if a.lifecycle_ready),
            },
            "rows": [a.to_dict() for a in results],
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(text, encoding="utf-8")
            print(f"✔ تم كتابة JSON إلى {args.out}")
        else:
            print(text)
    else:
        print_table(results, only_watch=args.only_watch)
        for a in results:
            if a.watchlist:
                print_symbol_report(a)

    if args.report:
        targets = [a.symbol for a in results if a.watchlist] or syms
        written = write_reports(targets, hub, REPORTS_DIR, analyses={a.symbol: a for a in results})
        wl = watchlist_markdown(results, src)
        stamp = datetime.now().strftime("%Y%m%d")
        (REPORTS_DIR / f"watchlist_{stamp}.md").write_text(wl, encoding="utf-8")
        print(f"✔ تم توليد {len(written)} تقريراً في {REPORTS_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
