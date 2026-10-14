"""اختبارات مزوّد البيانات التجريبي + واجهة الـ API."""
from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.providers.demo import DemoProvider
from app.server import app


@pytest.fixture(scope="module")
def demo() -> DemoProvider:
    return DemoProvider()


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


# ------------------------- سلامة البيانات ------------------------- #
def test_daily_ohlc_is_valid(demo):
    for sym in ["JZ", "NVEX", "HLIO", "SRNA"]:
        df = demo.daily(sym, 300)
        assert len(df) > 100
        assert (df["high"] >= df["low"]).all()
        assert (df["high"] >= df[["open", "close"]].max(axis=1) - 1e-9).all()
        assert (df["low"] <= df[["open", "close"]].min(axis=1) + 1e-9).all()
        assert (df["close"] > 0).all()
        assert (df["volume"] > 0).all()


def test_4h_matches_daily(demo):
    daily = demo.daily("JZ", 300)
    h4 = demo.intraday_4h("JZ")
    day = daily.index[-3]
    dd = daily.loc[day]
    sub = h4[(h4.index >= day) & (h4.index < day + pd.Timedelta(days=1))]
    assert len(sub) == 2
    assert sub["high"].max() == pytest.approx(dd["high"], abs=1e-6)
    assert sub["low"].min() == pytest.approx(dd["low"], abs=1e-6)
    assert sub["open"].iloc[0] == pytest.approx(dd["open"], abs=1e-6)
    assert sub["close"].iloc[-1] == pytest.approx(dd["close"], abs=1e-6)
    assert sub["volume"].sum() == pytest.approx(dd["volume"], abs=1e-3)


def test_demo_is_deterministic():
    a = DemoProvider().daily("JZ", 100)["close"].tolist()
    b = DemoProvider().daily("JZ", 100)["close"].tolist()
    assert a == b


def test_splits_profile_short_news(demo):
    ev = demo.splits("JZ")
    assert ev and ev[0].ratio_str == "1-for-8"
    prof = demo.profile("JZ")
    assert prof.shares_out and prof.float_shares and prof.float_shares < prof.shares_out
    sd = demo.short_data("JZ")
    assert sd and sd.short_pct_float and sd.days_to_cover
    assert demo.catalysts("JZ")
    assert demo.dilution("JZ")
    assert "JZ" in demo.universe(100)


def test_filler_symbols_have_varied_splits(demo):
    in_window = 0
    for sym in demo.universe(100):
        for ev in demo.splits(sym):
            if ev.age_days <= 60:
                in_window += 1
    assert in_window >= 3, "يجب أن يحتوي الكون التجريبي على عدة تقسيمات داخل النافذة"


# ------------------------- الـ API ------------------------- #
def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["data"]["mode_effective"] == "demo"
    assert data["data"]["providers"]


def test_index_and_assets(client):
    assert client.get("/").status_code == 200
    assert client.get("/assets/styles.css").status_code == 200
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/vendor/lightweight-charts.standalone.production.js").status_code == 200


def test_strategy_endpoint(client):
    r = client.get("/api/strategy")
    p = r.json()["params"]
    assert p["rs_lookback_days"] == 60
    assert p["min_drop_pct"] == 30
    assert p["rsi_oversold"] == 30
    assert sum(p["weights"].values()) == 100


def test_universe_and_demo_metadata(client):
    assert client.get("/api/universe?limit=10").json()["symbols"]
    assert client.get("/api/demo-metadata").json()["scenarios"]


def test_scan_cycle(client):
    assert client.post("/api/scan/start?limit=12").json()["started"] is True
    import time
    for _ in range(40):
        st = client.get("/api/scan/status").json()
        if not st["running"]:
            break
        time.sleep(0.5)
    res = client.get("/api/scan/results").json()
    assert res["summary"]["scanned"] >= 10
    assert res["summary"]["with_reverse_split"] >= 1
    assert res["rows"]


def test_analysis_and_chart(client):
    client.post("/api/scan/start?limit=12")
    import time
    for _ in range(40):
        if not client.get("/api/scan/status").json()["running"]:
            break
        time.sleep(0.5)
    a = client.get("/api/analysis/JZ").json()
    assert a["rs_ratio_str"] == "1-for-8"
    assert a["levels"]["targets"]["t2_618"] > a["levels"]["stop"]
    for tf in ("1D", "4H"):
        c = client.get(f"/api/chart/JZ?tf={tf}").json()
        assert c["candles"], tf
        assert len(c["overlays"]["ema9"]) == len(c["candles"])
        assert c["markers"]


def test_report_endpoints(client):
    assert "تقرير تحليلي" in client.get("/api/report/JZ?fmt=md").text
    assert "<html" in client.get("/api/report/JZ?fmt=html").text.lower()
    assert client.get("/api/report/JZ?fmt=json").json()["symbol"] == "JZ"
