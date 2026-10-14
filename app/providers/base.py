"""واجهة مزوّدي البيانات + أنواع البيانات المشتركة.

Data-provider contracts. Every provider returns the same normalized objects so
the strategy engine never cares where the numbers came from.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from ..config import CACHE_DIR, settings


# --------------------------------------------------------------------------- #
#  أنواع البيانات
# --------------------------------------------------------------------------- #
@dataclass
class ReverseSplitEvent:
    symbol: str
    date: pd.Timestamp          # تاريخ السريان الفعلي
    ratio_str: str = ""         # مثال: "1-for-8"
    factor: float = 1.0         # 8.0 لعكس 1:8
    source: str = "unknown"
    url: str = ""
    announced_at: pd.Timestamp | None = None

    @property
    def age_days(self) -> int:
        return int((pd.Timestamp.now().normalize() - self.date.normalize()).days)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["date"] = self.date.strftime("%Y-%m-%d")
        d["age_days"] = self.age_days
        d["announced_at"] = self.announced_at.strftime("%Y-%m-%d") if self.announced_at else None
        return d


@dataclass
class TickerProfile:
    symbol: str
    name: str = ""
    exchange: str = ""
    sector: str = ""
    industry: str = ""
    shares_out: float | None = None
    float_shares: float | None = None
    market_cap: float | None = None
    avg_volume: float | None = None
    avg_dollar_volume: float | None = None
    insider_pct: float | None = None
    institutional_pct: float | None = None
    source: str = "unknown"
    as_of: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ShortData:
    symbol: str
    as_of: str | None = None
    short_shares: float | None = None
    short_pct_float: float | None = None
    days_to_cover: float | None = None
    prev_short_shares: float | None = None
    short_volume_ratio: float | None = None
    source: str = "unknown"

    @property
    def change_pct(self) -> float | None:
        if self.short_shares and self.prev_short_shares:
            return (self.short_shares / self.prev_short_shares - 1) * 100
        return None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["change_pct"] = self.change_pct
        return d


@dataclass
class Catalyst:
    """حدث/خبر مستقبلي أو حديث - إيجابي أو سلبي أو محايد."""
    symbol: str
    date: str
    type: str            # offering | earnings | compliance | clinical | contract | split | other
    title: str
    sentiment: str = "neutral"   # positive | negative | neutral
    impact: str = "medium"       # high | medium | low
    source: str = ""
    url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DilutionSignal:
    symbol: str
    kind: str                  # ATM | S-1 | S-3 | 424B5 | warrant | convertible | reverse_split_notice
    date: str
    title: str
    severity: str = "medium"   # high | medium | low
    url: str = ""
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProviderStatus:
    name: str
    ok: bool
    detail: str = ""
    latency_ms: int = 0
    source_kind: str = "live"   # live | demo | cache

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
#  كاش بسيط على القرص
# --------------------------------------------------------------------------- #
class DiskCache:
    def __init__(self, folder: Path = CACHE_DIR, ttl_minutes: int | None = None):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.ttl = (ttl_minutes or settings.cache_ttl_minutes) * 60

    def _path(self, key: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)[:120]
        return self.folder / f"{safe}.json"

    def get(self, key: str) -> Any | None:
        p = self._path(key)
        if not p.exists():
            return None
        if self.ttl and (time.time() - p.stat().st_mtime) > self.ttl:
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def set(self, key: str, value: Any) -> None:
        try:
            self._path(key).write_text(
                json.dumps(value, ensure_ascii=False, default=str), encoding="utf-8"
            )
        except (OSError, TypeError):
            pass


# --------------------------------------------------------------------------- #
#  تحويل الشموع إلى JSON للواجهة
# --------------------------------------------------------------------------- #
def df_to_candles(df: pd.DataFrame, limit: int | None = None,
                  extra_cols: list[str] | None = None) -> list[dict[str, Any]]:
    """تحويل DataFrame إلى قائمة شموع بصيغة lightweight-charts."""
    if df is None or df.empty:
        return []
    sub = df.tail(limit) if limit else df
    extra_cols = extra_cols or []
    out: list[dict[str, Any]] = []
    for idx, row in sub.iterrows():
        item: dict[str, Any] = {
            "time": int(pd.Timestamp(idx).timestamp()),
            "open": round(float(row["open"]), 6),
            "high": round(float(row["high"]), 6),
            "low": round(float(row["low"]), 6),
            "close": round(float(row["close"]), 6),
            "volume": float(row.get("volume", 0) or 0),
        }
        for col in extra_cols:
            if col in row and not pd.isna(row[col]):
                item[col] = round(float(row[col]), 6)
        out.append(item)
    return out


# --------------------------------------------------------------------------- #
#  البروتوكول
# --------------------------------------------------------------------------- #
class MarketDataProvider(Protocol):
    name: str
    kind: str

    def daily(self, symbol: str, lookback_days: int = 260) -> pd.DataFrame: ...
    def intraday_4h(self, symbol: str, lookback_days: int = 90) -> pd.DataFrame: ...
    def splits(self, symbol: str) -> list[ReverseSplitEvent]: ...
    def profile(self, symbol: str) -> TickerProfile: ...
    def short_data(self, symbol: str) -> ShortData | None: ...
    def catalysts(self, symbol: str) -> list[Catalyst]: ...
    def dilution(self, symbol: str) -> list[DilutionSignal]: ...
    def universe(self, limit: int = 100) -> list[str]: ...


class BaseProvider:
    """أساس مشترك: كاش + تتبّع الحالة."""

    name = "base"
    kind = "live"

    def __init__(self, cache: DiskCache | None = None):
        self.cache = cache or DiskCache()
        self.status = ProviderStatus(name=self.name, ok=True, source_kind=self.kind)

    # أدوات مساعدة -------------------------------------------------------- #
    def _cached(self, key: str, producer, ttl_minutes: int | None = None):
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        val = producer()
        if val is not None:
            payload = val
            if isinstance(val, pd.DataFrame):
                payload = {
                    "__df__": True,
                    "index": [str(i) for i in val.index],
                    "columns": list(map(str, val.columns)),
                    "data": val.astype(object).where(pd.notna(val), None).values.tolist(),
                }
            self.cache.set(key, payload)
        return val

    def empty_df(self) -> pd.DataFrame:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    @staticmethod
    def _normalize_ohlcv(df: pd.DataFrame | None) -> pd.DataFrame:
        if df is None or len(df) == 0:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        out = df.copy()
        out.columns = [str(c).lower() for c in out.columns]
        need = ["open", "high", "low", "close", "volume"]
        for col in need:
            if col not in out.columns:
                out[col] = 0.0 if col == "volume" else out.get("close", 0.0)
        out = out[need]
        out = out[~out.index.duplicated(keep="last")].sort_index()
        out = out.dropna(subset=["close"])
        out["volume"] = out["volume"].fillna(0.0)
        return out


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def date_n_days_ago(days: int) -> pd.Timestamp:
    return pd.Timestamp((utcnow() - timedelta(days=days)).date())
