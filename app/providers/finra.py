"""مزوّد FINRA — نسبة البيع على المكشوف اليومية (Reg SHO) + مراكز البيع نصف الشهرية.

مصدر مجاني بدون مفاتيح:
  • حجم البيع المكشوف اليومي (Reg SHO):
      https://cdn.finra.org/equity/regsho/daily/CNMSshvolYYYYMMDD.txt
    الملف مفصول بـ | : Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market
  • مراكز البيع المكشوف نصف الشهرية (إن توفّرت النسخة العامة):
      https://cdn.finra.org/equity/otcmarket/biweekly/shrtYYYYMMDD.txt

الإخراج يُدمج مع بيانات Yahoo عند الحاجة (Hub).
"""
from __future__ import annotations

import io
from datetime import date, timedelta

import pandas as pd
import requests

from .base import BaseProvider, ProviderStatus, ShortData
from ..config import settings

REG_SHO_MARKETS = ["CNMS"]          # CNMS = Nasdaq/NYSE/consolidated tape
REG_SHO_URL = "https://cdn.finra.org/equity/regsho/daily/{market}shvol{yyyymmdd}.txt"
SHORT_INTEREST_URL = "https://cdn.finra.org/equity/otcmarket/biweekly/shrt{yyyymmdd}.txt"


class FINRAProvider(BaseProvider):
    name = "finra"
    kind = "live"

    def __init__(self, cache=None):
        super().__init__(cache)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": settings.sec_user_agent})

    def _get_text(self, url: str, timeout: int = 15) -> str | None:
        try:
            r = requests.get(url, headers={"User-Agent": settings.sec_user_agent}, timeout=timeout)
            if r.status_code == 404:
                return None
            if r.status_code >= 400:
                self.status = ProviderStatus(self.name, False, f"HTTP {r.status_code}")
                return None
            self.status = ProviderStatus(self.name, True)
            return r.text
        except Exception as exc:  # noqa: BLE001
            self.status = ProviderStatus(self.name, False, str(exc)[:150])
            return None

    # ---------------------------------------------------------------- #
    def short_volume_ratio(self, symbol: str, sessions: int = 10) -> tuple[float | None, list[dict]]:
        """متوسط نسبة حجم البيع المكشوف لآخر N جلسات (0..1) + التفاصيل اليومية."""
        symbol = symbol.upper()
        key = f"finra_regsho_{symbol}"
        cached = self.cache.get(key)
        if cached:
            return cached.get("avg"), cached.get("rows", [])

        rows: list[dict] = []
        for market in REG_SHO_MARKETS:
            d = date.today()
            fetched = 0
            while fetched < sessions and (date.today() - d).days < sessions * 3 + 5:
                if d.weekday() < 5:
                    day_key = d.strftime("%Y%m%d")
                    text = self._get_text(REG_SHO_URL.format(market=market, yyyymmdd=day_key))
                    if text:
                        parsed = _find_symbol(text, symbol)
                        if parsed:
                            rows.append({"date": str(d), **parsed})
                            fetched += 1
                d -= timedelta(days=1)
            break
        ratios = [r["ratio"] for r in rows if r.get("ratio") is not None]
        avg = round(sum(ratios) / len(ratios), 4) if ratios else None
        self.cache.set(key, {"avg": avg, "rows": rows})
        return avg, rows

    def short_data_from_regsho(self, symbol: str, float_shares: float | None = None) -> ShortData | None:
        avg, rows = self.short_volume_ratio(symbol)
        if avg is None:
            return None
        return ShortData(
            symbol=symbol.upper(),
            as_of=rows[0]["date"] if rows else None,
            short_pct_float=None,
            days_to_cover=None,
            short_volume_ratio=avg,
            source="finra-regsho",
        )

    def short_interest(self, symbol: str) -> ShortData | None:
        """محاولة قراءة ملف مراكز البيع نصف الشهري (قد يتطلب إذن FINRA API)."""
        symbol = symbol.upper()
        d = date.today()
        for _ in range(24):  # ابحث عن آخر ملف متاح (نصف شهري)
            if d.weekday() < 5:
                text = self._get_text(SHORT_INTEREST_URL.format(yyyymmdd=d.strftime("%Y%m%d")))
                if text:
                    row = _find_short_interest(text, symbol)
                    if row:
                        return ShortData(
                            symbol=symbol, as_of=row.get("date"), short_shares=row.get("short_shares"),
                            short_pct_float=None, days_to_cover=row.get("days_to_cover"),
                            prev_short_shares=row.get("prev_short_shares"), source="finra-short-interest",
                        )
            d -= timedelta(days=1)
        return None

    def profile(self, symbol: str):
        from .base import TickerProfile
        return TickerProfile(symbol=symbol.upper(), source=self.name)

    def splits(self, symbol: str):
        return []

    def daily(self, symbol: str, lookback_days: int = 260):
        return self.empty_df()

    def intraday_4h(self, symbol: str, lookback_days: int = 90):
        return self.empty_df()

    def catalysts(self, symbol: str):
        return []

    def dilution(self, symbol: str):
        return []

    def universe(self, limit: int = 100):
        return []


def _find_symbol(text: str, symbol: str) -> dict | None:
    try:
        df = pd.read_csv(io.StringIO(text), sep="|", dtype=str)
    except Exception:  # noqa: BLE001
        return None
    cols = {c.strip().lower(): c for c in df.columns}
    sym_col = cols.get("symbol")
    if not sym_col:
        return None
    row = df[df[sym_col].str.upper() == symbol.upper()]
    if row.empty:
        return None
    r = row.iloc[0]

    def num(name: str) -> float | None:
        c = cols.get(name)
        if not c:
            return None
        try:
            return float(str(r[c]).replace(",", ""))
        except (TypeError, ValueError):
            return None

    sv = num("shortvolume")
    tv = num("totalvolume")
    ratio = (sv / tv) if (sv is not None and tv) else None
    return {
        "short_volume": sv, "total_volume": tv,
        "ratio": round(ratio, 4) if ratio is not None else None,
    }


def _find_short_interest(text: str, symbol: str) -> dict | None:
    try:
        df = pd.read_csv(io.StringIO(text), sep="|", dtype=str)
    except Exception:  # noqa: BLE001
        return None
    sym_col = None
    for c in df.columns:
        if c.strip().lower() in {"symbol", "symbolcode"}:
            sym_col = c
            break
    if sym_col is None:
        return None
    row = df[df[sym_col].str.upper() == symbol.upper()]
    if row.empty:
        return None
    r = row.iloc[0]

    def num(*names) -> float | None:
        for n in names:
            for c in df.columns:
                if c.strip().lower().replace(" ", "") == n:
                    try:
                        return float(str(r[c]).replace(",", ""))
                    except (TypeError, ValueError):
                        continue
        return None

    return {
        "date": str(_pick(r, df.columns, "settlementdate", "date")),
        "short_shares": num("currentshortpositionquantity", "shortshares"),
        "prev_short_shares": num("previousshortpositionquantity"),
        "days_to_cover": num("daystocoverquantity", "daystocover"),
    }


def _pick(row, columns, *names):
    for n in names:
        for c in columns:
            if c.strip().lower().replace(" ", "") == n:
                return row[c]
    return ""
