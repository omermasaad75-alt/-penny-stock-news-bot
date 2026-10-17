"""طبقة الرسوم — تجهيز حِزم الشموع والمؤشرات للواجهة (Lightweight Charts)."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..analytics import indicators as ind
from ..config import STRATEGY, StrategyParams
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


def build_chart_payload(symbol: str, tf: str, hub: DataHub,
                        params: StrategyParams = STRATEGY, limit: int = 220,
                        analysis: Any = None) -> dict[str, Any]:
    """حِزمة كاملة لرسم فني: شموع + متوسطات + RSI/MACD/Stoch + أحداث + مستويات."""
    tf = (tf or "1D").upper().replace("D", "D")
    is_intraday = tf in {"4H", "1H", "60M"}
    raw = hub.intraday_4h(symbol, 90) if is_intraday else hub.daily(symbol, 400)
    if raw is None or raw.empty:
        return {"symbol": symbol, "tf": tf, "candles": [], "error": "لا توجد بيانات"}
    df = ind.add_indicators(raw, params)
    df = df.tail(limit)
    candles, volumes = _bars(df, tf)

    overlays = {
        "ema9": _line(df["ema9"], tf),
        "ema20": _line(df["ema20"], tf),
        "ema30": _line(df["ema30"], tf),
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

    # الأحداث على المخطط (تقسيم عكسي / قمة / قاع / اختراق)
    markers: list[dict] = []
    events = hub.splits(symbol)
    ev, _ = _pick(events, params)
    first_t = candles[0]["time"] if candles else None
    if ev is not None:
        t = str(pd.Timestamp(ev.date).date())
        if tf in {"4H", "1H", "60M"}:
            t = _nearest_time(df, ev.date, tf) or first_t
        markers.append({"time": t, "position": "aboveBar", "color": "#58a6ff",
                        "shape": "arrowDown", "text": f"تقسيم عكسي {ev.ratio_str}"})
        seg = df.loc[df.index >= pd.Timestamp(ev.date)]
        if len(seg) > 3:
            peak_i = seg["high"].idxmax()
            markers.append({"time": _t(peak_i, tf), "position": "aboveBar", "color": "#d29922",
                            "shape": "circle", "text": f"قمة {float(seg['high'].max()):.2f}"})
            after_peak = df.loc[df.index >= peak_i]
            if len(after_peak) > 3:
                if analysis is not None and getattr(analysis, "bottom_price", None):
                    bot_price, bot_date = analysis.bottom_price, analysis.bottom_date
                else:
                    bot_price, bot_date = float(after_peak["low"].min()), None
                    bot_i = after_peak["low"].idxmin()
                    bot_date = _t(bot_i, tf)
                if bot_price is not None and bot_date:
                    markers.append({"time": bot_date, "position": "belowBar", "color": "#3fb950",
                                    "shape": "arrowUp", "text": f"قاع {float(bot_price):.2f}"})

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


def _pick(events, params):
    from .engine import pick_reverse_split
    return pick_reverse_split(events, params, None)
