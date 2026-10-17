"""عقد الماسح المرن: الفلاتر الخمسة فقط، التدقيق، الترتيب، والتخصيص."""
from __future__ import annotations

import csv

import pandas as pd
import pytest

from app.analytics.technical_score import technical_components
from app.config import STRATEGY
from app.providers.hub import DataHub
from app.screener import (
    ScreenConfigError,
    build_params,
    load_overrides,
    parse_weights,
    rank_eligible,
    rules_snapshot,
    write_scan_csv,
)
from app.strategy import engine as eng


@pytest.fixture(scope="module")
def hub() -> DataHub:
    return DataHub(mode="demo")


def test_core_filter_audit_is_exactly_five_and_matches_eligibility(hub):
    for symbol in ("JZ", "NVEX", "BIOK", "QLGN", "UPC"):
        a = eng.analyze_symbol(symbol, hub)
        assert [f["key"] for f in a.core_filters] == list(eng.CORE_FILTER_KEYS)
        assert all({"ok", "display", "rule_ar", "data_available"} <= set(f) for f in a.core_filters)
        expected = bool(a.rs_found and not a.rejections and all(f["ok"] for f in a.core_filters))
        assert a.eligible == expected, symbol
    assert eng.analyze_symbol("JZ", hub).eligible is True
    assert eng.analyze_symbol("QLGN", hub).core_filters[0]["ok"] is False
    assert eng.analyze_symbol("BIOK", hub).core_filters[-1]["ok"] is False


def test_core_override_changes_eligibility_without_extra_filters(hub):
    params = build_params({"max_price": 1.5})
    a = eng.analyze_symbol("JZ", hub, params)
    assert a.eligible is False
    assert [f["key"] for f in a.core_filters] == list(eng.CORE_FILTER_KEYS)
    assert a.core_filters[0]["ok"] is False
    assert a.rejection_codes == ["price"]
    # بقية الفلاتر صحيحة: الرفض من فلتر السعر فقط لا من شرط سادس.
    assert [f["ok"] for f in a.core_filters[1:]] == [True, True, True, True]


def test_build_params_is_isolated_and_merges_weights():
    before = dict(STRATEGY.weights)
    params = build_params({"weights": {"volume": 50}, "rsi_oversold": 25})
    assert params.weights["volume"] == 50
    assert params.weights["patterns"] == before["patterns"]
    assert params.rsi_oversold == 25
    assert STRATEGY.weights == before
    assert params.weights is not STRATEGY.weights


@pytest.mark.parametrize("bad", [
    {"bogus": 1},
    {"min_price": 5, "max_price": 1},
    {"min_drop_pct": 130},
    {"rsi_oversold": 0},
    {"max_short_shares": 0},
    {"weights": {"volume": -1}},
])
def test_invalid_config_is_rejected(bad):
    with pytest.raises(ScreenConfigError):
        build_params(bad)


def test_weight_parser_and_example_config():
    assert parse_weights("volume=25,patterns=35,support=25,ema=15")["ema"] == 15
    overrides = load_overrides("config/screener.example.json")
    params = build_params(overrides)
    assert params.min_price == 1.0 and params.max_price == 5.0
    assert params.max_short_shares == 20000
    assert params.weights["patterns"] == 35


def test_rank_eligible_is_score_descending(hub):
    results = [eng.analyze_symbol(s, hub) for s in ("JZ", "NVEX", "BIOK", "QLGN")]
    ranked = rank_eligible(results)
    assert [a.symbol for a in ranked] == ["JZ", "NVEX"]
    assert ranked[0].score >= ranked[1].score
    assert all(a.eligible for a in ranked)


def test_csv_main_table_only_eligible_sorted(hub, tmp_path):
    results = [eng.analyze_symbol(s, hub) for s in ("NVEX", "BIOK", "JZ")]
    path = tmp_path / "scan.csv"
    count = write_scan_csv(path, results)
    assert count == 2
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    assert [r["الرمز"] for r in rows] == ["JZ", "NVEX"]
    assert rows[0]["مؤهل"] == "نعم"
    assert rows[0]["فلتر السعر 1–5$"] == "نعم"
    assert rows[0]["فلتر الأسهم المكشوفة <20,000"] == "نعم"


def test_csv_all_keeps_rejected_for_diagnostics(hub, tmp_path):
    results = [eng.analyze_symbol(s, hub) for s in ("JZ", "BIOK")]
    path = tmp_path / "all.csv"
    assert write_scan_csv(path, results, include_rejected=True) == 2
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    rejected = next(r for r in rows if r["الرمز"] == "BIOK")
    assert rejected["مؤهل"] == "لا"
    assert rejected["أسباب الاستبعاد"]


def test_volume_dryup_uses_base_phase_even_after_latest_volume_spike():
    idx = pd.bdate_range("2026-01-01", periods=60)
    df = pd.DataFrame({
        "low": [10.0] * 60, "close": [10.1] * 60,
        "volume": [1000.0] * 55 + [5000.0] * 5,
    }, index=idx)
    # لا يوجد جفاف في آخر 3 جلسات، لكن قاعدة القاع كانت جافة (0.4).
    parts = technical_components(df, [], STRATEGY, base_dryup=0.4)
    volume = next(p for p in parts if p[0] == "volume")
    assert volume[2] == 100
    assert "0.40" in volume[4]


def test_rules_snapshot_declares_only_five_and_optional_signals():
    snap = rules_snapshot(STRATEGY)
    assert [f["key"] for f in snap["core_filters"]] == list(eng.CORE_FILTER_KEYS)
    assert snap["eligibility"] == "five_hard_filters_only"
    assert set(snap["optional_score_weights"]) == {"volume", "patterns", "support", "ema"}
    assert "double_bottom" in snap["optional_signals_never_reject"]


def test_cli_writes_ranked_main_table_csv_and_json(tmp_path):
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    csv_path = tmp_path / "main.csv"
    json_path = tmp_path / "main.json"
    env = {**os.environ, "RSS_DATA_MODE": "demo"}
    proc = subprocess.run(
        [sys.executable, "-m", "app.scanner", "--symbols", "NVEX,BIKO,JZ",
         "--mode", "demo", "--json", "--csv", str(csv_path), "--out", str(json_path)],
        capture_output=True, text=True, env=env, cwd=str(root), timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["rules"]["eligibility"] == "five_hard_filters_only"
    assert [r["symbol"] for r in payload["rows"]] == ["JZ", "NVEX"]
    assert all(len(r["core_filters"]) == 5 for r in payload["rows"])
    assert list(csv.DictReader(csv_path.open(encoding="utf-8-sig")))[0]["الرمز"] == "JZ"


def test_cli_rejects_unknown_flag_config(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    cfg = tmp_path / "bad.json"
    cfg.write_text('{"sixth_filter": true}', encoding="utf-8")
    env = {**os.environ, "RSS_DATA_MODE": "demo"}
    proc = subprocess.run(
        [sys.executable, "-m", "app.scanner", "--symbols", "JZ", "--mode", "demo",
         "--config", str(cfg)],
        capture_output=True, text=True, env=env, cwd=str(root), timeout=120,
    )
    assert proc.returncode == 2
    assert "غير معروف" in proc.stderr
