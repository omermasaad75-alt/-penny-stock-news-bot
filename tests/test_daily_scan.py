"""اختبارات المسح اليومي وبناء موقع النشر (tools/daily_scan.py)."""
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
def test_sequence_state_for_ready_symbol(tool):
    from app.providers.hub import get_hub
    from app.config import STRATEGY
    from app.strategy import engine as eng

    hub = get_hub(mode="demo", refresh=True)
    a = eng.analyze_symbol("JZ", hub, STRATEGY)
    done, label, color = tool.sequence_state(a)
    assert len(a.lifecycle_checks) == 6
    assert 0 <= done <= 6
    assert label and color in {"ok", "warn", "bad", "muted"}


def test_dots_html_has_six_dots(tool):
    from app.config import STRATEGY
    from app.providers.hub import get_hub
    from app.strategy import engine as eng

    hub = get_hub(mode="demo")
    a = eng.analyze_symbol("BRTX", hub, STRATEGY)
    html = tool.dots_html(a)
    assert html.count('<i class="dot') == 6
    assert "<span class=\"dots\">" in html


def test_summarize_counts(tool):
    from app.config import STRATEGY
    from app.providers.hub import get_hub
    from app.strategy import engine as eng

    hub = get_hub(mode="demo")
    results = [eng.analyze_symbol(s, hub, STRATEGY) for s in ("JZ", "NVEX", "UPC")]
    s = tool.summarize(results)
    assert s["scanned"] == 3
    assert s["ready"] == len(s["ready_symbols"])
    assert s["with_reverse_split"] <= 3
    assert s["gam_candidates"] == len(s["gam_symbols"])
    assert set(s["eligible_symbols"]).isdisjoint(s["gam_symbols"])


# ------------------------- مسار كامل (CLI) ------------------------- #
def test_cli_builds_publishable_site(tmp_path):
    """يشغّل الأداة فعلياً (وضع تجريبي) ويتحقّق من ملفات النشر."""
    site = tmp_path / "site"
    env = {**os.environ, "RSS_DATA_MODE": "demo"}
    env.pop("GITHUB_ACTIONS", None)  # isolate local test-only subprocess, not deployment
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
    assert "لوحة رصد التقسيم العكسي" in html
    assert "dashboard.html" in html and "data/latest.csv" in html
    assert "$" not in html.split("<style>")[0]           # لا تبقى عناصر قالب غير مُستبدلة
    assert html.count('<i class="dot') >= 6                # نقاط دورة الحياة في الجدول

    payload = json.loads(data.read_text(encoding="utf-8"))
    assert payload["summary"]["scanned"] == 5
    assert len(payload["rows"]) == 5
    assert payload["mode"] in {"demo", "live"}

    header = csv.read_text(encoding="utf-8-sig").splitlines()[0]
    assert "الرمز" in header and "النقاط الفنية" in header
    assert "مرشح GAM" in header and "أسباب GAM" in header
    assert "GAM" in html and "غير مؤهل" in html

    stamp = payload["stamp"]
    assert (site / "history" / f"{stamp}.json").exists()
    history = json.loads((site / "history" / "index.json").read_text(encoding="utf-8"))
    assert history and history[0]["stamp"] == stamp
    assert list((site / "reports").glob("*.html"))


def test_dataset_separates_gam_from_eligible_and_watchlist(tool, tmp_path):
    from app.config import STRATEGY
    from app.providers.hub import get_hub
    from app.strategy import engine as eng

    hub = get_hub(mode="demo", refresh=True)
    results = [eng.analyze_symbol(s, hub, STRATEGY) for s in ("JZ", "BIOK", "QLGN")]
    site = tmp_path / "site"
    started = tool.now_riyadh()
    sources = hub.sources_report()
    sources["coverage"] = tool.build_coverage(sources, results)
    payload = tool.write_dataset(site, results, sources, "demo", started)

    rows = {r["symbol"]: r for r in payload["rows"]}
    assert rows["JZ"]["eligible"] is True and rows["JZ"]["gam_candidate"] is False
    assert rows["BIOK"]["gam_candidate"] is False
    assert "short_shares" in rows["BIOK"]["rejection_codes"]
    assert rows["BIOK"]["eligible"] is False and rows["BIOK"]["watchlist"] is False
    assert rows["QLGN"]["last_close"] < STRATEGY.min_price
    assert rows["QLGN"]["gam_candidate"] is False
    assert payload["summary"]["gam_candidates"] == 0
    assert payload["gam_policy"]["historical_state_persisted"] is False
    assert payload["gam_policy"]["rsi_scope"] == "current_scan_price_series"

    csv_text = (site / "data" / "latest.csv").read_text(encoding="utf-8-sig")
    assert "مرشح GAM" in csv_text
    biok_line = next(line for line in csv_text.splitlines() if line.startswith("BIOK,"))
    assert "نعم — غير مؤهل" not in biok_line
