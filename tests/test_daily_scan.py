"""اختبارات المسح اليومي وبناء موقع النشر (tools/daily_scan.py) — الاستراتيجية الحالية."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_tool():
    spec = importlib.util.spec_from_file_location("daily_scan", ROOT / "tools" / "daily_scan.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tool():
    return _load_tool()


# ------------------------- وحدات مساعدة ------------------------- #
def test_dots_html_has_six_dots(tool):
    from app.providers.hub import get_hub
    from app.strategy import engine as eng

    hub = get_hub(mode="demo")
    a = eng.analyze_symbol("BRTX", hub)
    html = tool.dots_html(a)
    assert html.count('<i class="dot') == 6
    assert '<span class="dots">' in html


def test_summarize_counts(tool):
    from app.providers.hub import get_hub
    from app.strategy import engine as eng

    hub = get_hub(mode="demo")
    results = [eng.analyze_symbol(s, hub) for s in ("JZ", "TRVN", "IMPL", "UPC")]
    s = tool.summarize(results)
    assert s["scanned"] == 4
    assert s["ready"] == len(s["ready_symbols"])
    assert s["eligible"] == len(s["eligible_symbols"])
    assert s["watchlist"] == len(s["watchlist_symbols"])
    # الأهلية = NEAR أو READY؛ قائمة المتابعة تشملهما مع WATCH
    assert set(s["eligible_symbols"]) <= set(s["watchlist_symbols"])
    assert "gam_candidates" not in s


# ------------------------- مسار كامل (CLI) ------------------------- #
def test_cli_builds_publishable_site(tmp_path):
    """يشغّل الأداة فعلياً (وضع تجريبي) ويتحقّق من ملفات النشر."""
    site = tmp_path / "site"
    env = {**os.environ, "RSS_DATA_MODE": "demo"}
    env.pop("GITHUB_ACTIONS", None)  # تشغيل محلي للاختبار فقط، ليس نشراً
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "daily_scan.py"),
         "--site", str(site), "--limit", "5", "--mode", "demo",
         "--top-reports", "3", "--no-dashboard", "--test-only"],
        capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]

    index = site / "index.html"
    data = site / "data" / "latest.json"
    csv = site / "data" / "latest.csv"
    assert index.exists() and data.exists() and csv.exists()
    assert (site / ".nojekyll").exists() and (site / "manifest.json").exists()

    html = index.read_text(encoding="utf-8")
    assert "لوحة الاستراتيجية" in html
    assert "dashboard.html" in html and "data/latest.csv" in html
    assert "$" not in html.split("<style>")[0]           # لا تبقى عناصر قالب غير مُستبدلة
    assert html.count('<i class="dot') >= 6                # نقاط دورة الحياة في الجدول
    assert "GAM" not in html

    payload = json.loads(data.read_text(encoding="utf-8"))
    assert payload["summary"]["scanned"] == 5
    assert len(payload["rows"]) == 5
    assert payload["mode"] in {"demo", "live"}
    assert all(r["strategy_status"] in {"READY", "NEAR", "WATCH", "EXCLUDED"} for r in payload["rows"])

    header = csv.read_text(encoding="utf-8-sig").splitlines()[0]
    assert "الرمز" in header and "التقييم" in header and "الحالة" in header
    assert "الهدف 1 (+25%)" in header and "حجم المركز (أسهم)" in header
    assert "GAM" not in header

    stamp = payload["stamp"]
    assert (site / "history" / f"{stamp}.json").exists()
    history = json.loads((site / "history" / "index.json").read_text(encoding="utf-8"))
    assert history and history[0]["stamp"] == stamp
    assert list((site / "reports").glob("*.html"))


def test_dataset_status_maps_to_eligibility(tool, tmp_path):
    from app.providers.hub import get_hub
    from app.strategy import engine as eng

    hub = get_hub(mode="demo", refresh=True)
    results = [eng.analyze_symbol(s, hub) for s in ("JZ", "TRVN", "BIOK", "QLGN")]
    site = tmp_path / "site"
    started = tool.now_riyadh()
    sources = hub.sources_report()
    sources["coverage"] = tool.build_coverage(sources, results)
    payload = tool.write_dataset(site, results, sources, "demo", started)

    rows = {r["symbol"]: r for r in payload["rows"]}
    for sym, r in rows.items():
        assert r["eligible"] == (r["strategy_status"] in {"READY", "NEAR"}), sym
        assert r["watchlist"] == (r["strategy_status"] in {"READY", "NEAR", "WATCH"}), sym
    assert rows["TRVN"]["eligible"] is True
    assert rows["JZ"]["strategy_status"] == "WATCH"
    assert payload["summary"]["eligible"] == len(payload["summary"]["eligible_symbols"])
    assert "gam_policy" not in payload

    csv_text = (site / "data" / "latest.csv").read_text(encoding="utf-8-sig")
    trvn_line = next(line for line in csv_text.splitlines() if line.startswith("TRVN,"))
    assert "الهدف 1" not in trvn_line  # الترويسة فقط
    assert trvn_line.count(",") >= 20
