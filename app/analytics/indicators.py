"""مكتبة المؤشرات الفنية — Technical indicators (pure pandas/numpy).

كل الدوال تستقبل DataFrame بترتيب زمني تصاعدي والأعمدة:
    open, high, low, close, volume
وتُعيد Series/DataFrame بنفس الفهرس.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

OHLCV = ["open", "high", "low", "close", "volume"]


# --------------------------------------------------------------------------- #
#  المتوسطات
# --------------------------------------------------------------------------- #
def sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(length, min_periods=max(2, length // 2)).mean()


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False, min_periods=1).mean()


def wma(series: pd.Series, length: int) -> pd.Series:
    weights = np.arange(1, length + 1)
    return series.rolling(length).apply(
        lambda x: float(np.dot(x, weights) / weights.sum()), raw=True
    )


def hma(series: pd.Series, length: int = 21) -> pd.Series:
    """Hull Moving Average - أسرع في التقاط الانعكاس."""
    half, sqrt = max(1, length // 2), max(1, int(np.sqrt(length)))
    return wma(2 * wma(series, half) - wma(series, length), sqrt)


# --------------------------------------------------------------------------- #
#  الزخم
# --------------------------------------------------------------------------- #
def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """RSI بطريقة Wilder (تنعيم أسي بمعامل 1/length)."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    out = out.where(avg_loss != 0, 100.0)
    return out.clip(0, 100)


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return pd.DataFrame(
        {"macd": macd_line, "signal": signal_line, "hist": hist}, index=close.index
    )


def stochastic(
    df: pd.DataFrame, k: int = 14, d: int = 3, smooth: int = 3
) -> pd.DataFrame:
    low_k = df["low"].rolling(k, min_periods=max(2, k // 2)).min()
    high_k = df["high"].rolling(k, min_periods=max(2, k // 2)).max()
    rng = (high_k - low_k).replace(0.0, np.nan)
    raw_k = 100 * (df["close"] - low_k) / rng
    k_line = raw_k.rolling(smooth, min_periods=1).mean()
    d_line = k_line.rolling(d, min_periods=1).mean()
    return pd.DataFrame({"k": k_line, "d": d_line}, index=df.index)


def williams_r(df: pd.DataFrame, length: int = 14) -> pd.Series:
    hh = df["high"].rolling(length, min_periods=max(2, length // 2)).max()
    ll = df["low"].rolling(length, min_periods=max(2, length // 2)).min()
    rng = (hh - ll).replace(0.0, np.nan)
    return -100 * (hh - df["close"]) / rng


def cci(df: pd.DataFrame, length: int = 20) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    ma = tp.rolling(length, min_periods=max(2, length // 2)).mean()
    md = (tp - ma).abs().rolling(length, min_periods=max(2, length // 2)).mean()
    return (tp - ma) / (0.015 * md.replace(0.0, np.nan))


def mfi(df: pd.DataFrame, length: int = 14) -> pd.Series:
    """Money Flow Index - RSI بحجم التداول (ممتاز لرصد جفاف البيع)."""
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    flow = tp * df["volume"]
    up = flow.where(tp.diff() > 0, 0.0)
    down = flow.where(tp.diff() < 0, 0.0)
    up_sum = up.rolling(length, min_periods=length).sum()
    down_sum = down.rolling(length, min_periods=length).sum()
    ratio = up_sum / down_sum.replace(0.0, np.nan)
    out = 100 - (100 / (1 + ratio))
    return out.where(down_sum != 0, 100.0).clip(0, 100)


# --------------------------------------------------------------------------- #
#  التقلب
# --------------------------------------------------------------------------- #
def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1.0 / length, adjust=False, min_periods=1).mean()


def atr_pct(df: pd.DataFrame, length: int = 14) -> pd.Series:
    return 100 * atr(df, length) / df["close"].replace(0.0, np.nan)


def bollinger(close: pd.Series, length: int = 20, mult: float = 2.0) -> pd.DataFrame:
    mid = sma(close, length)
    dev = close.rolling(length, min_periods=max(2, length // 2)).std(ddof=0)
    upper, lower = mid + mult * dev, mid - mult * dev
    width = 100 * (upper - lower) / mid.replace(0.0, np.nan)
    return pd.DataFrame(
        {"mid": mid, "upper": upper, "lower": lower, "width": width, "dev": dev},
        index=close.index,
    )


def keltner(df: pd.DataFrame, length: int = 20, mult: float = 1.5) -> pd.DataFrame:
    mid = ema(df["close"], length)
    band = atr(df, length) * mult
    return pd.DataFrame(
        {"mid": mid, "upper": mid + band, "lower": mid - band}, index=df.index
    )


def squeeze(df: pd.DataFrame, length: int = 20) -> pd.Series:
    """TTM Squeeze مبسط: تقارب بولنجر داخل كيلتنر = ضغط تقلب."""
    bb = bollinger(df["close"], length)
    kc = keltner(df, length)
    return (bb["upper"] < kc["upper"]) & (bb["lower"] > kc["lower"])


# --------------------------------------------------------------------------- #
#  الاتجاه
# --------------------------------------------------------------------------- #
def adx(df: pd.DataFrame, length: int = 14) -> pd.DataFrame:
    up_move = df["high"].diff()
    down_move = -df["low"].diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm = pd.Series(plus_dm, index=df.index)
    minus_dm = pd.Series(minus_dm, index=df.index)
    tr = true_range(df)
    atr_s = tr.ewm(alpha=1.0 / length, adjust=False, min_periods=1).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1.0 / length, adjust=False, min_periods=1).mean() / atr_s.replace(0.0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1.0 / length, adjust=False, min_periods=1).mean() / atr_s.replace(0.0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    adx_line = dx.ewm(alpha=1.0 / length, adjust=False, min_periods=1).mean()
    return pd.DataFrame(
        {"adx": adx_line, "plus_di": plus_di, "minus_di": minus_di}, index=df.index
    )


# --------------------------------------------------------------------------- #
#  الحجم والسيولة
# --------------------------------------------------------------------------- #
def obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff().fillna(0.0))
    return (direction * df["volume"]).fillna(0.0).cumsum()


def cmf(df: pd.DataFrame, length: int = 20) -> pd.Series:
    rng = (df["high"] - df["low"]).replace(0.0, np.nan)
    mfm = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / rng
    mfv = mfm.fillna(0.0) * df["volume"]
    return mfv.rolling(length, min_periods=2).sum() / df["volume"].rolling(
        length, min_periods=2
    ).sum().replace(0.0, np.nan)


def rvol(df: pd.DataFrame, length: int = 20) -> pd.Series:
    """الحجم النسبي = حجم الجلسة / متوسط الحجم."""
    avg = df["volume"].rolling(length, min_periods=3).mean()
    return df["volume"] / avg.replace(0.0, np.nan)


def dollar_volume(df: pd.DataFrame, length: int = 20) -> pd.Series:
    return (df["close"] * df["volume"]).rolling(length, min_periods=3).mean()


def anchored_vwap(df: pd.DataFrame, anchor_index) -> pd.Series:
    """VWAP مرتكز على تاريخ معين (مثلاً يوم التقسيم أو يوم القاع)."""
    if anchor_index not in df.index:
        return pd.Series(np.nan, index=df.index)
    pos = df.index.get_loc(anchor_index)
    sub = df.iloc[pos:]
    tp = (sub["high"] + sub["low"] + sub["close"]) / 3.0
    pv = (tp * sub["volume"]).cumsum()
    vv = sub["volume"].cumsum().replace(0.0, np.nan)
    vwap = pv / vv
    return vwap.reindex(df.index)


# --------------------------------------------------------------------------- #
#  القنوات والاتجاه الخطي
# --------------------------------------------------------------------------- #
def regression_channel(df: pd.DataFrame, length: int = 30, mult: float = 2.0) -> pd.DataFrame:
    """قناة انحدار خطي على اللوغاريتم (تصلح للأسهم ذات التغيرات النسبية).

    تُستخدم لرصد "القناة الهابطة" وتحديد لحظة اختراق سقفها العلوي.
    """
    close = df["close"].where(df["close"] > 0)
    logp = np.log(close)
    x = np.arange(length, dtype=float)
    x_mean = x.mean()
    x_var = ((x - x_mean) ** 2).sum()

    def _fit(win: np.ndarray) -> tuple[float, float, float]:
        y = win
        if np.isnan(y).any() or not np.isfinite(y).all():
            return np.nan, np.nan, np.nan
        y_mean = y.mean()
        slope = ((x - x_mean) * (y - y_mean)).sum() / x_var
        intercept = y_mean - slope * length  # نُسقط القيمة عند آخر نقطة
        resid = y - (intercept + slope * x)
        return intercept, slope, float(np.std(resid))

    vals = logp.to_numpy(dtype=float)
    intercepts = np.full(len(vals), np.nan)
    slopes = np.full(len(vals), np.nan)
    sigmas = np.full(len(vals), np.nan)
    for i in range(length - 1, len(vals)):
        ic, sl, sg = _fit(vals[i - length + 1 : i + 1])
        intercepts[i], slopes[i], sigmas[i] = ic, sl, sg

    out = pd.DataFrame(index=df.index)
    out["slope"] = slopes
    out["sigma"] = sigmas
    # خط الانحدار عند آخر شمعة = exp(intercept + slope*length)
    out["mid"] = np.exp(intercepts + slopes * (length - 1))
    band = mult * sigmas
    out["upper"] = np.exp(intercepts + slopes * (length - 1) + band)
    out["lower"] = np.exp(intercepts + slopes * (length - 1) - band)
    out["slope_pct_per_bar"] = (np.exp(slopes) - 1.0) * 100.0
    return out


# --------------------------------------------------------------------------- #
#  القمم والقيعان (بنية السوق)
# --------------------------------------------------------------------------- #
def swing_points(df: pd.DataFrame, left: int = 2, right: int = 2) -> pd.DataFrame:
    """تحديد القمم والقيعان المؤقتة (fractal pivots)."""
    high, low = df["high"], df["low"]
    n = len(df)
    is_high = np.zeros(n, dtype=bool)
    is_low = np.zeros(n, dtype=bool)
    hv, lv = high.to_numpy(), low.to_numpy()
    for i in range(left, n - right):
        win_h = hv[i - left : i + right + 1]
        win_l = lv[i - left : i + right + 1]
        if hv[i] == win_h.max() and (win_h == hv[i]).sum() == 1:
            is_high[i] = True
        if lv[i] == win_l.min() and (win_l == lv[i]).sum() == 1:
            is_low[i] = True
    out = pd.DataFrame(index=df.index)
    out["swing_high"] = np.where(is_high, high, np.nan)
    out["swing_low"] = np.where(is_low, low, np.nan)
    return out


def count_higher_lows(df: pd.DataFrame, since_index, left: int = 1, right: int = 1) -> int:
    """عدد القيعان الصاعدة منذ تاريخ معين (دليل تجميع مبكر)."""
    swings = swing_points(df, left=left, right=right)
    lows = swings["swing_low"].dropna()
    try:
        lows = lows.loc[lows.index >= since_index]
    except TypeError:
        return 0
    vals = lows.to_list()
    return sum(1 for a, b in zip(vals, vals[1:]) if b > a)


def bullish_divergence(df: pd.DataFrame, rsi_series: pd.Series, lookback: int = 40) -> bool:
    """ديفرجنس إيجابي: قاع سعري أدنى مع قاع RSI أعلى."""
    sub = df.tail(lookback)
    rsi_sub = rsi_series.reindex(sub.index)
    swings = swing_points(sub, left=1, right=1)
    lows = swings["swing_low"].dropna()
    if len(lows) < 2:
        return False
    last_two = lows.iloc[-2:]
    idx = last_two.index
    try:
        p1, p2 = last_two.iloc[0], last_two.iloc[1]
        r1, r2 = rsi_sub.loc[idx[0]], rsi_sub.loc[idx[1]]
    except KeyError:
        return False
    if any(pd.isna(v) for v in (r1, r2)):
        return False
    return bool(p2 < p1 and r2 > r1 + 1.0)


# --------------------------------------------------------------------------- #
#  الحزمة الكاملة
# --------------------------------------------------------------------------- #
def add_indicators(df: pd.DataFrame, params=None) -> pd.DataFrame:
    """إضافة الحزمة الكاملة من المؤشرات إلى DataFrame."""
    p = params
    rsi_len = getattr(p, "rsi_period", 14)
    ema_f = getattr(p, "ema_fast", 9)
    ema_m = getattr(p, "ema_mid", 20)
    ema_s = getattr(p, "ema_slow", 50)

    out = df.copy()
    out["ema9"] = ema(out["close"], ema_f)
    out["ema20"] = ema(out["close"], ema_m)
    out["ema30"] = ema(out["close"], 30)
    out["ema50"] = ema(out["close"], ema_s)
    out["ema200"] = ema(out["close"], 200)
    out["sma20"] = sma(out["close"], 20)
    out["hma21"] = hma(out["close"], 21)
    out["rsi"] = rsi(out["close"], rsi_len)
    m = macd(out["close"])
    out["macd"], out["macd_signal"], out["macd_hist"] = m["macd"], m["signal"], m["hist"]
    out["atr"] = atr(out)
    out["atr_pct"] = atr_pct(out)
    bb = bollinger(out["close"])
    out["bb_mid"], out["bb_up"], out["bb_low"], out["bb_width"] = (
        bb["mid"], bb["upper"], bb["lower"], bb["width"],
    )
    out["bb_width_pct_rank"] = out["bb_width"].rolling(60, min_periods=10).rank(pct=True) * 100
    st = stochastic(out)
    out["stoch_k"], out["stoch_d"] = st["k"], st["d"]
    out["willr"] = williams_r(out)
    out["cci"] = cci(out)
    out["mfi"] = mfi(out)
    ad = adx(out)
    out["adx"], out["plus_di"], out["minus_di"] = ad["adx"], ad["plus_di"], ad["minus_di"]
    out["obv"] = obv(out)
    out["cmf"] = cmf(out)
    out["rvol"] = rvol(out)
    out["dollar_vol"] = dollar_volume(out)
    rc = regression_channel(out, 30)
    out["ch_mid"], out["ch_up"], out["ch_low"] = rc["mid"], rc["upper"], rc["lower"]
    out["ch_slope_pct"] = rc["slope_pct_per_bar"]
    out["sqz"] = squeeze(out)
    out["vol_sma20"] = sma(out["volume"], 20)
    return out
