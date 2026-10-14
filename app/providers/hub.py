"""مركز البيانات — DataHub: دمج المزوّدين مع سياسة صارمة للوضع الحي.

قواعد التشغيل:
  • demo : بيانات تجريبية مولّدة داخلياً (تعمل بدون انترنت) — للاختبارات فقط
  • live : بيانات حقيقية فقط (Yahoo + SEC + FINRA + RSS). أي فشل = استبعاد الرمز.
          لا رجوع إطلاقاً إلى DemoProvider في هذا الوضع.
  • auto : يُعامل كـ live حالياً (لمنع النشر غير المقصود لبيانات تجريبية).
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

# رموز معروفة بأنها وهمية/غير موجودة — تُرفض فوراً في الوضع الحي.
FAKE_SYMBOLS = frozenset({
    "NVEX", "GNSX", "SRNA", "BIOK", "RUBY",
})


class DataHub:
    def __init__(self, mode: str | None = None):
        requested = (mode or settings.data_mode or "demo").lower()
        # auto يُعامل كـ live لمنع التسرّب إلى الديمو في النشر.
        if requested == "auto":
            requested = "live"
        self.mode = requested
        self.demo = DemoProvider()
        self.yahoo = YahooProvider() if _HAS_YF else None
        self.sec = SECProvider()
        self.finra = FINRAProvider()
        self.news = NewsProvider()
        self._live_probe: bool | None = None
        self._probe_at: float = 0.0
        self.log: list[str] = []
        self.rejections: dict[str, str] = {}   # reason per symbol

    # ------------------------------------------------------------------ #
    #  تحديد الوضع
    # ------------------------------------------------------------------ #
    def _is_live_mode(self) -> bool:
        """الوضع الحي فقط (لا auto ترجع للديمو)."""
        return self.mode == "live"

    def live_available(self, force: bool = False) -> bool:
        """فحص توفر الاتصال بمزوّدي البيانات الحقيقية."""
        if not self._is_live_mode():
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

    @property
    def effective_mode(self) -> str:
        """يعيد 'live' فقط عندما نكون فعلاً في وضع live ويتوفر الاتصال.
        خلاف ذلك: في وضع live مع عدم توفر الاتصال ⇒ 'live-unavailable' (ليس 'demo')."""
        if self._is_live_mode():
            return "live" if self.live_available() else "live-unavailable"
        return "demo"

    def sources_report(self) -> dict:
        live = self._is_live_mode() and self.live_available()
        rows = []
        if self._is_live_mode():
            rows.append(self.yahoo.status.to_dict() if self.yahoo else
                        ProviderStatus("yahoo-finance", _HAS_YF, "yfinance غير مثبّت",
                                       source_kind="live").to_dict())
            rows.append(self.sec.status.to_dict())
            rows.append(self.finra.status.to_dict())
            rows.append(self.news.status.to_dict())
            # سجل الديمو كمعطّل
            rows.append(ProviderStatus(
                "demo-generator", False,
                "معطّل — الوضع الحي لا يستخدم بيانات تجريبية",
                source_kind="demo").to_dict())
        else:
            rows.append(ProviderStatus("demo-generator", True,
                                       "بيانات محاكية مولّدة داخلياً (وضع اختبار فقط)",
                                       source_kind="demo").to_dict())
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
            "log": self.log[-10:],
            "rejections": dict(self.rejections),
            "synthetic_count": 0 if self._is_live_mode() else -1,  # -1 = كله ديمو
        }

    def _reject(self, symbol: str, reason: str) -> None:
        """تسجيل سبب استبعاد رمز (يُستخدم في التقارير)."""
        key = symbol.upper()
        prev = self.rejections.get(key, "")
        self.rejections[key] = reason if not prev else f"{prev}؛ {reason}"
        self.log.append(f"استبعاد {key}: {reason}")

    # ------------------------------------------------------------------ #
    #  واجهات البيانات
    # ------------------------------------------------------------------ #
    def daily(self, symbol: str, lookback_days: int = 260) -> pd.DataFrame:
        """في الوضع live: بيانات حقيقية فقط. لا fallback للديمو."""
        sym = symbol.upper()
        if sym in FAKE_SYMBOLS and self._is_live_mode():
            self._reject(sym, "رمز وهمي غير موجود في السوق")
            return pd.DataFrame()
        if self._is_live_mode() and self.yahoo:
            try:
                df = self.yahoo.daily(sym, lookback_days)
            except Exception as exc:  # noqa: BLE001
                self._reject(sym, f"فشل جلب الأسعار اليومية: {exc}")
                return pd.DataFrame()
            if df is not None and len(df) > 30:
                return df
            self._reject(sym, "لا توجد بيانات سعر يومية حقيقية لهذا الرمز")
            return pd.DataFrame()
        # وضع demo
        return self.demo.daily(sym, lookback_days)

    def intraday_4h(self, symbol: str, lookback_days: int = 90) -> pd.DataFrame:
        sym = symbol.upper()
        if sym in FAKE_SYMBOLS and self._is_live_mode():
            return pd.DataFrame()
        if self._is_live_mode() and self.yahoo:
            try:
                df = self.yahoo.intraday_4h(sym, lookback_days)
            except Exception as exc:  # noqa: BLE001
                self._reject(sym, f"فشل جلب بيانات 4 ساعات: {exc}")
                return pd.DataFrame()
            if df is not None and len(df) > 30:
                return df
            return pd.DataFrame()
        return self.demo.intraday_4h(sym, lookback_days)

    def splits(self, symbol: str) -> list[ReverseSplitEvent]:
        sym = symbol.upper()
        events: list[ReverseSplitEvent] = []
        if self._is_live_mode():
            if self.yahoo:
                try:
                    events = self.yahoo.splits(sym) or []
                except Exception as exc:  # noqa: BLE001
                    self.log.append(f"فشل splits من yahoo لـ {sym}: {exc}")
            if not events:
                try:
                    events = self.sec.splits(sym) or []
                except Exception as exc:  # noqa: BLE001
                    self.log.append(f"فشل splits من sec لـ {sym}: {exc}")
            # لا fallback إلى demo في الوضع الحي — نرجع ما وجدناه (قد يكون [])
            return sorted(events, key=lambda e: e.date, reverse=True)
        events = self.demo.splits(sym) or []
        return sorted(events, key=lambda e: e.date, reverse=True)

    def profile(self, symbol: str) -> TickerProfile | None:
        sym = symbol.upper()
        if self._is_live_mode():
            prof = None
            if self.yahoo:
                try:
                    prof = self.yahoo.profile(sym)
                except Exception as exc:  # noqa: BLE001
                    self._reject(sym, f"فشل جلب الملف التعريفي: {exc}")
                    prof = None
                if prof and (not prof.name or prof.shares_out is None):
                    try:
                        sec_prof = self.sec.profile(sym)
                        if sec_prof and sec_prof.name:
                            prof.name = prof.name or sec_prof.name
                            if prof.shares_out is None and sec_prof.shares_out:
                                prof.shares_out = sec_prof.shares_out
                    except Exception:  # noqa: BLE001
                        pass
            if prof and prof.name:
                return prof
            self._reject(sym, "لا توجد بيانات تعريفية حقيقية لهذا الرمز")
            return None
        return self.demo.profile(sym)

    def short_data(self, symbol: str, float_shares: float | None = None) -> ShortData | None:
        sym = symbol.upper()
        if self._is_live_mode():
            sd = None
            if self.yahoo:
                try:
                    sd = self.yahoo.short_data(sym)
                except Exception as exc:  # noqa: BLE001
                    self.log.append(f"فشل short_data yahoo لـ {sym}: {exc}")
            try:
                regsho_list = self.finra.short_volume_ratio(sym, sessions=10) or []
                regsho = regsho_list[0] if regsho_list else None
            except Exception:  # noqa: BLE001
                regsho = None
            if sd is None and regsho is not None:
                sd = ShortData(symbol=sym, short_volume_ratio=regsho, source="finra-regsho")
            elif sd is not None and regsho is not None:
                sd.short_volume_ratio = regsho
                sd.source = f"{sd.source}+finra-regsho"
            # لا fallback للديمو — قد نعيد None (بيانات البيع المكشوف اختيارية)
            return sd
        return self.demo.short_data(sym)

    def catalysts(self, symbol: str) -> list[Catalyst]:
        sym = symbol.upper()
        out: list[Catalyst] = []
        if self._is_live_mode():
            for name, fn in (
                ("yahoo", lambda: self.yahoo.catalysts(sym) if self.yahoo else []),
                ("news", lambda: self.news.catalysts(sym)),
                ("sec", lambda: self.sec.catalysts(sym)),
            ):
                try:
                    out += fn() or []
                except Exception as exc:  # noqa: BLE001
                    self.log.append(f"فشل catalysts من {name} لـ {sym}: {exc}")
            # لا fallback للديمو
            seen, uniq = set(), []
            for c in sorted(out, key=lambda c: c.date, reverse=True):
                k = (c.title or "")[:70].lower()
                if k in seen:
                    continue
                seen.add(k)
                uniq.append(c)
            return uniq[:40]
        out = self.demo.catalysts(sym)
        return out

    def dilution(self, symbol: str) -> list[DilutionSignal]:
        sym = symbol.upper()
        out: list[DilutionSignal] = []
        if self._is_live_mode():
            for prov in (self.sec, self.news, self.yahoo):
                try:
                    out += prov.dilution(sym) or []
                except Exception:  # noqa: BLE001
                    continue
            seen, uniq = set(), []
            for d in sorted(out, key=lambda d: d.date, reverse=True):
                k = (d.title or "")[:70].lower()
                if k in seen:
                    continue
                seen.add(k)
                uniq.append(d)
            return uniq[:25]
        return self.demo.dilution(sym)

    def universe(self, limit: int = 60) -> list[str]:
        """كون السوق: في الوضع live يُبنى من ياهو + قوائم التكوين بعد التحقق."""
        if self._is_live_mode() and self.yahoo:
            try:
                syms = self.yahoo.universe(limit=limit)
            except Exception as exc:  # noqa: BLE001
                self.log.append(f"فشل بناء الكون من Yahoo: {exc}")
                syms = []
            # فلترة الرموز الوهمية المعروفة
            syms = [s for s in syms if s.upper() not in FAKE_SYMBOLS]
            if syms:
                return syms[:limit] if limit else syms
            self.log.append("تحذير: تعذّر بناء كون حقيقي — لن تُنشر بيانات تجريبية.")
            return []
        return self.demo.universe(limit=limit)

    def scenario_note(self, symbol: str) -> str:
        sym = symbol.upper()
        if self.effective_mode == "demo":
            return self.demo.scenario_note(sym)
        return ""


_HUB: DataHub | None = None


def get_hub(mode: str | None = None, refresh: bool = False) -> DataHub:
    global _HUB
    if _HUB is None or refresh or (mode and mode != _HUB.mode):
        _HUB = DataHub(mode)
    return _HUB
