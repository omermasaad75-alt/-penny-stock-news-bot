"""طبقة الرسوم — تجهيز حِزم الشموع والمؤشرات للواجهة (Lightweight Charts)."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..analytics import indicators as ind
from ..providers.hub import DataHub


def _t(idx, tf: str) -> Any:
    ts = pd.Timestamp(idx)
    return int(ts.timestamp()) if tf.upper() in {"4H", "1H", "60M"} else str(ts.date())


def _line(series: pd.Series, tf: str, nd: int = 4) -> list[dict]:
    out = []
    for idx, v in series.items():
        if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
            continue
        out.append({"time": _t(idx, tf), "value": round(float(v), nd)})
    return out


def _bars(df: pd.DataFrame, tf: str) -> tuple[list[dict], list[dict]]:
    candles, volumes = [], []
    prev_close = None
    for idx, row in df.iterrows():
        o, h, l, c = (float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]))
        v = float(row.get("volume", 0) or 0)
        color = "#3fb950" if (prev_close is None and c >= o) or (prev_close is not None and c >= prev_close) else "#f85149"
        candles.append({"time": _t(idx, tf), "open": round(o, 4), "high": round(h, 4),
                        "low": round(l, 4), "close": round(c, 4)})
        volumes.append({"time": _t(idx, tf), "value": v, "color": color + "66"})
        prev_close = c
    return candles, volumes


def build_chart_payload(symbol: str, tf: str, hub: DataHub, limit: int = 220,
                        analysis: Any = None) -> dict[str, Any]:
    """حِزمة كاملة لرسم فني: شموع + متوسطات + مؤشرات + علامات النمط."""
    tf = (tf or "1D").upper().replace("D", "D")
    is_intraday = tf in {"4H", "1H", "60M"}
    raw = hub.intraday_4h(symbol, 90) if is_intraday else hub.daily(symbol, 400)
    if raw is None or raw.empty:
        return {"symbol": symbol, "tf": tf, "candles": [], "error": "لا توجد بيانات"}
    df = ind.add_indicators(raw, None)
    df = df.tail(limit)
    candles, volumes = _bars(df, tf)

    overlays = {
        "ema9": _line(df["ema9"], tf),
        "ema20": _line(df["ema20"], tf),
        "ema50": _line(df["ema50"], tf),
        "ema200": _line(df["ema200"], tf),
        "bb_up": _line(df["bb_up"], tf),
        "bb_mid": _line(df["bb_mid"], tf),
        "bb_low": _line(df["bb_low"], tf),
        "ch_up": _line(df["ch_up"], tf),
        "ch_mid": _line(df["ch_mid"], tf),
        "ch_low": _line(df["ch_low"], tf),
    }
    oscillators = {
        "rsi": _line(df["rsi"], tf, 2),
        "macd": _line(df["macd"], tf, 4),
        "macd_signal": _line(df["macd_signal"], tf, 4),
        "macd_hist": _line(df["macd_hist"], tf, 4),
        "stoch_k": _line(df["stoch_k"], tf, 2),
        "stoch_d": _line(df["stoch_d"], tf, 2),
        "mfi": _line(df["mfi"], tf, 2),
        "atr_pct": _line(df["atr_pct"], tf, 2),
        "rvol": _line(df["rvol"], tf, 2),
    }

    # علامات النمط (القاعان) — من نتيجة التحليل إن وُجدت
    markers: list[dict] = []
    pat = (getattr(analysis, "pattern", None) or {}) if analysis is not None else {}
    if pat.get("found"):
        series_markers = (getattr(analysis, "series", None) or {}).get("markers", {})
        for key, label, color, pos, shape in (
            ("low1", "قاع 1", "#58a6ff", "belowBar", "circle"),
            ("low2", "قاع 2", "#3fb950", "belowBar", "arrowUp"),
        ):
            date = series_markers.get(f"{key}_date")
            if tf in {"4H", "1H", "60M"}:
                date = _nearest_time(df, pat.get(f"{key}_date") or date, tf) if date else None
            if date:
                markers.append({"time": date, "position": pos, "color": color, "shape": shape,
                                "text": f"{label} {float(pat[key]):.2f}"})

    return {
        "symbol": symbol.upper(), "tf": tf, "candles": candles, "volume": volumes,
        "overlays": overlays, "oscillators": oscillators, "markers": markers,
        "meta": {
            "bars": len(candles),
            "last_close": round(float(df["close"].iloc[-1]), 4),
            "last_rsi": round(float(df["rsi"].iloc[-1]), 2) if pd.notna(df["rsi"].iloc[-1]) else None,
            "from": candles[0]["time"] if candles else None,
            "to": candles[-1]["time"] if candles else None,
            "rvol": round(float(df["rvol"].iloc[-1]), 2) if pd.notna(df["rvol"].iloc[-1]) else None,
        },
    }


def _nearest_time(df: pd.DataFrame, date, tf: str) -> str | None:
    try:
        target = pd.Timestamp(date)
        sub = df.loc[df.index >= target]
        if len(sub) == 0:
            sub = df.tail(1)
        return _t(sub.index[0], tf)
    except Exception:  # noqa: BLE001
        return None
