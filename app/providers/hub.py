"""مركز البيانات — DataHub: دمج المزوّدين مع التدرّج الآمن (fallback).

قواعد التشغيل:
  • demo : بيانات تجريبية مولّدة داخلياً (تعمل بدون انترنت)
  • live : بيانات حقيقية (Yahoo + SEC + FINRA + RSS) مع تدرّج إلى التجريبي عند الفشل
  • auto : يحاول الحقيقي أولاً، وإن فشل الاتصال يرجع للتجريبي ويسجّل السبب
"""
from __future__ import annotations

import time

import pandas as pd

from ..config import settings
from .base import (
    Catalyst,
    DilutionSignal,
    ProviderStatus,
    ReverseSplitEvent,
    ShortData,
    TickerProfile,
)
from .demo import DemoProvider
from .finra import FINRAProvider
from .news import NewsProvider
from .sec import SECProvider
from .yahoo import YahooProvider

try:
    import yfinance as _yf  # noqa: F401
    _HAS_YF = True
except Exception:  # noqa: BLE001
    _HAS_YF = False


class DataHub:
    def __init__(self, mode: str | None = None):
        self.mode = (mode or settings.data_mode or "demo").lower()
        self.demo = DemoProvider()
        self.yahoo = YahooProvider() if _HAS_YF else None
        self.sec = SECProvider()
        self.finra = FINRAProvider()
        self.news = NewsProvider()
        self._live_probe: bool | None = None
        self._probe_at: float = 0.0
        self.log: list[str] = []

    # ------------------------------------------------------------------ #
    #  تحديد الوضع
    # ------------------------------------------------------------------ #
    def live_available(self, force: bool = False) -> bool:
        if self.mode == "demo":
            return False
        if self.yahoo is None:
            return False
        if not force and self._live_probe is not None and (time.time() - self._probe_at) < 900:
            return self._live_probe
        ok = False
        try:
            hist = self.yahoo._ticker("SPY").history(period="5d", interval="1d", raise_errors=False)
            ok = hist is not None and len(hist) > 0
        except Exception as exc:  # noqa: BLE001
            self.log.append(f"تعذّر الاتصال بمزوّد السوق: {exc}")
            ok = False
        self._live_probe = ok
        self._probe_at = time.time()
        return ok

    def _use_live(self) -> bool:
        if self.mode == "demo":
            return False
        if self.mode == "live":
            return self.live_available()
        return self.live_available()   # auto

    @property
    def effective_mode(self) -> str:
        return "live" if self._use_live() else "demo"

    def sources_report(self) -> dict:
        live = self._use_live()
        rows = [ProviderStatus("demo-generator", True, "بيانات محاكية مولّدة داخلياً",
                               source_kind="demo").to_dict()]
        if live:
            rows.append(self.yahoo.status.to_dict() if self.yahoo else
                        ProviderStatus("yahoo-finance", _HAS_YF, "yfinance غير مثبّت").to_dict())
            rows.append(self.sec.status.to_dict())
            rows.append(self.finra.status.to_dict())
            rows.append(self.news.status.to_dict())
        else:
            for nm, note in (
                ("yahoo-finance", "غير مفعّل في وضع البيانات التجريبي"),
                ("sec-edgar", "غير مفعّل في وضع البيانات التجريبي"),
                ("finra", "غير مفعّل في وضع البيانات التجريبي"),
                ("news-rss", "غير مفعّل في وضع البيانات التجريبي"),
            ):
                rows.append(ProviderStatus(nm, False, note, source_kind="live").to_dict())
        return {
            "mode_setting": self.mode,
            "mode_effective": self.effective_mode,
            "has_yfinance": _HAS_YF,
            "providers": rows,
            "log": self.log[-5:],
        }

    # ------------------------------------------------------------------ #
    #  واجهات البيانات
    # ------------------------------------------------------------------ #
    def daily(self, symbol: str, lookback_days: int = 260) -> pd.DataFrame:
        if self._use_live() and self.yahoo:
            df = self.yahoo.daily(symbol, lookback_days)
            if df is not None and len(df) > 30:
                return df
        return self.demo.daily(symbol, lookback_days)

    def intraday_4h(self, symbol: str, lookback_days: int = 90) -> pd.DataFrame:
        if self._use_live() and self.yahoo:
            df = self.yahoo.intraday_4h(symbol, lookback_days)
            if df is not None and len(df) > 30:
                return df
        return self.demo.intraday_4h(symbol, lookback_days)

    def splits(self, symbol: str) -> list[ReverseSplitEvent]:
        events: list[ReverseSplitEvent] = []
        if self._use_live() and self.yahoo:
            events = self.yahoo.splits(symbol) or []
        if not events and self._use_live():
            events = self.sec.splits(symbol) or []
        if not events:
            events = self.demo.splits(symbol) or []
        return sorted(events, key=lambda e: e.date, reverse=True)

    def profile(self, symbol: str) -> TickerProfile:
        prof = None
        if self._use_live() and self.yahoo:
            prof = self.yahoo.profile(symbol)
            if prof and prof.shares_out is None:
                sec_prof = self.sec.profile(symbol)
                if sec_prof and sec_prof.name:
                    prof.name = prof.name or sec_prof.name
        if not prof or (prof.shares_out is None and not prof.name):
            prof = self.demo.profile(symbol)
        return prof

    def short_data(self, symbol: str, float_shares: float | None = None) -> ShortData | None:
        if self._use_live():
            sd = self.yahoo.short_data(symbol) if self.yahoo else None
            regsho = None
            try:
                regsho = self.finra.short_volume_ratio(symbol, sessions=10)[0]
            except Exception:  # noqa: BLE001
                regsho = None
            if sd is None and regsho is not None:
                sd = ShortData(symbol=symbol.upper(), short_volume_ratio=regsho, source="finra-regsho")
            elif sd is not None and regsho is not None:
                sd.short_volume_ratio = regsho
                sd.source = f"{sd.source}+finra-regsho"
            if sd is not None:
                return sd
        return self.demo.short_data(symbol)

    def catalysts(self, symbol: str) -> list[Catalyst]:
        out: list[Catalyst] = []
        if self._use_live():
            try:
                out += self.yahoo.catalysts(symbol) if self.yahoo else []
            except Exception:  # noqa: BLE001
                pass
            try:
                out += self.news.catalysts(symbol)
            except Exception:  # noqa: BLE001
                pass
            try:
                out += self.sec.catalysts(symbol)
            except Exception:  # noqa: BLE001
                pass
        if not out:
            out = self.demo.catalysts(symbol)
        seen, uniq = set(), []
        for c in sorted(out, key=lambda c: c.date, reverse=True):
            k = (c.title or "")[:70].lower()
            if k in seen:
                continue
            seen.add(k)
            uniq.append(c)
        return uniq[:40]

    def dilution(self, symbol: str) -> list[DilutionSignal]:
        out: list[DilutionSignal] = []
        if self._use_live():
            for prov in (self.sec, self.news, self.yahoo):
                try:
                    out += prov.dilution(symbol) or []
                except Exception:  # noqa: BLE001
                    continue
        if not out:
            out = self.demo.dilution(symbol)
        seen, uniq = set(), []
        for d in sorted(out, key=lambda d: d.date, reverse=True):
            k = (d.title or "")[:70].lower()
            if k in seen:
                continue
            seen.add(k)
            uniq.append(d)
        return uniq[:25]

    def universe(self, limit: int = 60) -> list[str]:
        if self._use_live() and self.yahoo:
            syms = self.yahoo.universe(limit=limit)
            if syms:
                return syms
        return self.demo.universe(limit=limit)

    def scenario_note(self, symbol: str) -> str:
        if self.effective_mode == "demo":
            return self.demo.scenario_note(symbol)
        return ""


_HUB: DataHub | None = None


def get_hub(mode: str | None = None, refresh: bool = False) -> DataHub:
    global _HUB
    if _HUB is None or refresh or (mode and mode != _HUB.mode):
        _HUB = DataHub(mode)
    return _HUB
