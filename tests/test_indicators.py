"""اختبارات المؤشرات الفنية — صحة الحسابات والحدود."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.analytics import indicators as ind


@pytest.fixture()
def df() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    n = 260
    idx = pd.bdate_range("2025-01-01", periods=n)
    close = 3.0 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    high = close * (1 + np.abs(rng.normal(0, 0.01, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.01, n)))
    open_ = close * (1 + rng.normal(0, 0.005, n))
    vol = rng.integers(100_000, 2_000_000, n).astype(float)
    return pd.DataFrame({"open": open_, "high": np.maximum.reduce([high, open_, close]),
                         "low": np.minimum.reduce([low, open_, close]), "close": close,
                         "volume": vol}, index=idx)


def test_rsi_bounds(df):
    r = ind.rsi(df["close"], 14).dropna()
    assert r.between(0, 100).all()
    # سلسلة صاعدة بالكامل ⇒ RSI مرتفع
    up = pd.Series(np.linspace(1, 5, 60))
    assert ind.rsi(up, 14).iloc[-1] > 90


def test_ema_and_sma(df):
    e = ind.ema(df["close"], 9)
    s = ind.sma(df["close"], 20)
    assert len(e) == len(df) and e.notna().all()
    assert s.iloc[-1] == pytest.approx(df["close"].tail(20).mean(), rel=1e-9)


def test_atr_positive(df):
    a = ind.atr(df, 14).dropna()
    assert (a > 0).all()
    assert (ind.atr_pct(df).dropna() > 0).all()


def test_macd_relationship(df):
    m = ind.macd(df["close"])
    assert {"macd", "signal", "hist"} <= set(m.columns)
    assert np.allclose((m["macd"] - m["signal"]).dropna(), m["hist"].dropna(), atol=1e-9)


def test_bollinger_order(df):
    bb = ind.bollinger(df["close"], 20)
    last = bb.dropna().iloc[-1]
    assert last["lower"] <= last["mid"] <= last["upper"]
    assert last["width"] >= 0


def test_regression_channel_downtrend():
    idx = pd.bdate_range("2025-01-01", periods=80)
    close = pd.Series(np.linspace(10, 3, 80), index=idx)
    df = pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99,
                       "close": close, "volume": 1e6}, index=idx)
    rc = ind.regression_channel(df, 30)
    assert rc["slope_pct_per_bar"].dropna().iloc[-1] < 0
    assert rc["upper"].dropna().iloc[-1] > rc["lower"].dropna().iloc[-1]


def test_swing_points_detects_extremes():
    idx = pd.bdate_range("2025-01-01", periods=15)
    close = pd.Series([1, 2, 3, 4, 5, 4, 3, 2, 1, 2, 3, 4, 5, 4, 3], dtype=float, index=idx)
    df = pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 100}, index=idx)
    sw = ind.swing_points(df, left=2, right=2)
    assert sw["swing_high"].notna().any()
    assert sw["swing_low"].notna().any()


def test_stochastic_and_mfi_bounds(df):
    st = ind.stochastic(df).dropna()
    assert st["k"].between(-1, 101).all()
    m = ind.mfi(df).dropna()
    assert m.between(0, 100).all()


def test_add_indicators_full_pack(df):
    out = ind.add_indicators(df)
    for col in ["ema9", "ema20", "ema50", "rsi", "macd", "atr", "bb_up", "stoch_k",
                "adx", "obv", "cmf", "rvol", "ch_up", "sqz", "vol_sma20"]:
        assert col in out.columns, col
    assert len(out) == len(df)


def test_higher_lows_count():
    idx = pd.bdate_range("2025-01-01", periods=30)
    vals = [5, 4, 3, 2, 1, 2, 2.5, 3, 3.5, 4]
    vals += [3.2, 3.0, 2.8, 3.1, 3.6, 4.0, 4.4, 4.8, 5.2, 5.6]
    vals += [4.8, 4.4, 4.2, 4.5, 5.0, 5.4, 5.8, 6.2, 6.6, 7.0]
    s = pd.Series(vals, dtype=float, index=idx)
    df = pd.DataFrame({"open": s, "high": s, "low": s, "close": s, "volume": 100}, index=idx)
    assert ind.count_higher_lows(df, df.index[10]) >= 1
