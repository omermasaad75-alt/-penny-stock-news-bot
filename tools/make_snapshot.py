"""توليد «نسخة ثابتة» من اللوحة في ملف HTML واحد يعمل بلا مخدم وبلا إنترنت.

يجمع كل بيانات اللوحة (المسح + تحليل كل رمز + مخططات 1D/4H) وملفات الواجهة
(CSS + JS + مكتبة الرسوم) داخل ملف واحد قابل للفتح على أي جهاز أو إرساله.

الاستخدام:
    python3 tools/make_snapshot.py --url http://localhost:8000 --out docs/offline/rs_terminal_snapshot.html
"""

from __future__ import annotations

import argparse
import io
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as r:      # noqa: S310 (عنوان محلي)
        return json.loads(r.read().decode("utf-8"))


def build(url: str, out: Path, max_symbols: int = 40, badge: str | None = None) -> Path:
    health = get(f"{url}/api/health")
    strategy = get(f"{url}/api/strategy")
    results = get(f"{url}/api/scan/results")
    demo = {"scenarios": []}

    rows = results.get("rows") or []
    symbols = [r["symbol"] for r in rows][:max_symbols]
    analysis: dict[str, dict] = {}
    charts: dict[str, dict] = {}
    for sym in symbols:
        try:
            analysis[sym] = get(f"{url}/api/analysis/{sym}")
        except Exception as exc:                             # noqa: BLE001
            print(f"تخطّي التحليل {sym}: {exc}")
        charts[sym] = {}
        for tf in ("1D", "4H"):
            try:
                charts[sym][tf] = get(f"{url}/api/chart/{sym}?tf={tf}")
            except Exception as exc:                         # noqa: BLE001
                print(f"تخطّي المخطط {sym} {tf}: {exc}")

    snapshot = {"health": health, "strategy": strategy, "results": results,
                "demo": demo, "analysis": analysis, "charts": charts}

    css = (WEB / "assets" / "styles.css").read_text(encoding="utf-8")
    app = (WEB / "assets" / "app.js").read_text(encoding="utf-8")
    lwc = (WEB / "vendor" / "lightweight-charts.standalone.production.js").read_text(encoding="utf-8")
    html = (WEB / "index.html").read_text(encoding="utf-8")

    # نستبدل الروابط الخارجية بالمحتوى المضمّن
    html = html.replace('<link rel="stylesheet" href="assets/styles.css">',
                        f"<style>\n{css}\n</style>")
    html = html.replace('<link rel="stylesheet" href="/assets/styles.css">',
                        f"<style>\n{css}\n</style>")
    html = html.replace('<script src="/vendor/lightweight-charts.standalone.production.js"></script>',
                        f"<script>\n{lwc}\n</script>")
    html = html.replace('<script src="/assets/app.js"></script>',
                        "<script>window.__SNAPSHOT__ = "
                        + json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
                        + ";</script>\n<script>\n" + app + "\n</script>")
    html = html.replace("<title>", "<title>[نسخة ثابتة] ", 1)
    # نحدد شارة الوضع بناءً على البيانات الفعلية
    mode_eff = (results.get("mode") or (health.get("data") or {}).get("mode_effective") or "demo")
    synth = sum(1 for r in (results.get("rows") or []) if r.get("synthetic"))
    if mode_eff == "live" and synth == 0:
        badge_html = '<span id="modeBadge" class="badge badge-live">نسخة ثابتة — 🟢 بيانات حقيقية</span>'
    elif mode_eff == "live":
        badge_html = '<span id="modeBadge" class="badge badge-demo">نسخة ثابتة — 🟠 مختلطة</span>'
    else:
        badge_html = '<span id="modeBadge" class="badge badge-demo">نسخة ثابتة — 🔴 بيانات تجريبية</span>'
    html = html.replace('<span id="modeBadge" class="badge badge-demo">وضع البيانات: تجريبي</span>',
                        badge_html)

    out.parent.mkdir(parents=True, exist_ok=True)
    io.open(out, "w", encoding="utf-8").write(html)
    size_mb = out.stat().st_size / 1e6
    print(f"تم إنشاء النسخة الثابتة: {out} ({size_mb:.2f} MB) — {len(symbols)} رمز، "
          f"{sum(len(v) for v in charts.values())} مخطط")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="نسخة ثابتة من لوحة رصد التقسيم العكسي")
    ap.add_argument("--url", default="http://localhost:8000", help="عنوان المخدم العامل")
    ap.add_argument("--out", default="docs/offline/rs_terminal_snapshot.html", help="ملف الخرج")
    ap.add_argument("--max-symbols", type=int, default=40)
    args = ap.parse_args()
    build(args.url.rstrip("/"), Path(args.out), args.max_symbols)


if __name__ == "__main__":
    main()
