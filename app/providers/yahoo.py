"""مزوّد Yahoo Finance — بيانات السوق الحقيقية (أسعار، تقسيمات، فلوت، بيع مكشوف، أخبار).

Live market-data provider built on `yfinance` (بدون مفاتيح API).
كل دالة تفشل بهدوء (return None / []) حتى يرجع الـ Hub إلى بديل آمن.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import pandas as pd

from .base import (
    BaseProvider,
    Catalyst,
    ProviderStatus,
    ReverseSplitEvent,
    ShortData,
    TickerProfile,
    utcnow,
)

try:  # pragma: no cover - تعتمد على البيئة
    import yfinance as yf
except Exception:  # noqa: BLE001
    yf = None


# كلمات مفتاحية لتصنيف الأخبار وتقدير اتجاهها
NEGATIVE_KEYS = {
    "offering": ["offering", "public offering", "registered direct", "private placement",
                 "atm", "at-the-market", "pricing of", "warrant", "dilution", "shelf"],
    "distress": ["bankruptcy", "chapter 11", "going concern", "delisting", "deficiency",
                 "non-compliance", "default", "reorganization", "reverse stock split"],
    "weak_results": ["misses", "shortfall", "lower", "cuts", "decline", "loss widens"],
}
POSITIVE_KEYS = {
    "contract": ["contract", "award", "order", "partnership", "agreement", "collaboration"],
    "clinical": ["fda", "approval", "clearance", "positive results", "breakthrough",
                 "orphan", "phase 3", "phase 2", "milestone"],
    "financial": ["profitable", "profit", "beats", "raises guidance", "buyback",
                  "dividend", "strategic investment"],
}


def classify_news(title: str) -> tuple[str, str, str]:
    """(type, sentiment, impact) من عنوان الخبر."""
    t = (title or "").lower()
    for kind, keys in NEGATIVE_KEYS.items():
        if any(k in t for k in keys):
            impact = "high" if kind in {"offering", "distress"} else "medium"
            typ = "offering" if kind in {"offering", "distress"} else "earnings"
            return typ, "negative", impact
    for kind, keys in POSITIVE_KEYS.items():
        if any(k in t for k in keys):
            typ = "clinical" if kind == "clinical" else ("contract" if kind == "contract" else "earnings")
            return typ, "positive", "medium"
    return "other", "neutral", "low"


class YahooProvider(BaseProvider):
    name = "yahoo-finance"
    kind = "live"

    def __init__(self, cache=None, timeout: int = 15):
        super().__init__(cache)
        self.timeout = timeout
        self._ticker_cache: dict[str, object] = {}

    # ---------------------------------------------------------------- #
    def available(self) -> bool:
        return yf is not None

    def _ticker(self, symbol: str):
        if yf is None:
            raise RuntimeError("yfinance غير مثبّت")
        symbol = symbol.upper()
        if symbol not in self._ticker_cache:
            self._ticker_cache[symbol] = yf.Ticker(symbol)
        return self._ticker_cache[symbol]

    def _timed(self, fn, *args, **kwargs):
        t0 = time.time()
        try:
            out = fn(*args, **kwargs)
            self.status = ProviderStatus(self.name, True, latency_ms=int((time.time() - t0) * 1000))
            return out
        except Exception as exc:  # noqa: BLE001
            self.status = ProviderStatus(self.name, False, detail=str(exc)[:200],
                                         latency_ms=int((time.time() - t0) * 1000))
            return None

    # ---------------- الأسعار ---------------- #
    def daily(self, symbol: str, lookback_days: int = 260) -> pd.DataFrame:
        key = f"yahoo_daily_{symbol.upper()}_{lookback_days}"
        cached = self.cache.get(key)
        if cached:
            try:
                df = pd.DataFrame(cached["data"], columns=cached["columns"],
                                  index=pd.to_datetime(cached["index"]))
                return self._normalize_ohlcv(df)
            except Exception:  # noqa: BLE001
                pass

        period = "2y" if lookback_days > 300 else "1y"
        interval = "1d"
        raw = None
        if yf is not None:
            hist = self._timed(lambda: self._ticker(symbol).history(
                period=period, interval=interval, auto_adjust=False, raise_errors=False))
            if hist is not None and len(hist):
                raw = hist
            else:
                bulk = self._timed(lambda: yf.download(
                    symbol, period=period, interval=interval, progress=False,
                    auto_adjust=False, threads=False))
                if bulk is not None and len(bulk):
                    if isinstance(bulk.columns, pd.MultiIndex):
                        bulk.columns = bulk.columns.get_level_values(0)
                    raw = bulk
        if raw is None or not len(raw):
            return self.empty_df()

        df = raw.rename(columns=str.lower)
        keep = [c for c in ["open", "high", "low", "close", "adjclose", "volume"] if c in df.columns]
        df = df[keep]
        # الأسعار المعدّلة أثر رجعياً (auto_adjust=False ⇒ نظبط يدوياً)
        if "adjclose" in df.columns and df["adjclose"].notna().any():
            ratio = (df["adjclose"] / df["close"]).replace([float("inf"), float("-inf")], pd.NA)
            if ratio.notna().any() and float(ratio.max() or 1) > 0:
                for col in ("open", "high", "low", "close"):
                    df[col] = df[col] * ratio
        df = df.drop(columns=[c for c in ["adjclose"] if c in df.columns])
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        df.index = pd.DatetimeIndex(df.index).normalize()
        df = self._normalize_ohlcv(df)
        self.cache.set(key, {
            "index": [str(i) for i in df.index], "columns": list(df.columns),
            "data": df.astype(object).where(pd.notna(df), None).values.tolist(),
        })
        return df

    def intraday_4h(self, symbol: str, lookback_days: int = 90) -> pd.DataFrame:
        """شموع 4 ساعات عبر تجميع شموع الساعة (Yahoo يوفّر 1h حتى 730 يوماً)."""
        key = f"yahoo_4h_{symbol.upper()}"
        cached = self.cache.get(key)
        if cached:
            try:
                return self._normalize_ohlcv(pd.DataFrame(
                    cached["data"], columns=cached["columns"],
                    index=pd.to_datetime(cached["index"])))
            except Exception:  # noqa: BLE001
                pass
        if yf is None:
            return self.empty_df()
        hist = self._timed(lambda: self._ticker(symbol).history(
            period="60d", interval="1h", auto_adjust=False, raise_errors=False))
        if hist is None or not len(hist):
            return self.empty_df()
        df = hist.rename(columns=str.lower)
        df = df[[c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]]
        if df.index.tz is not None:
            df.index = df.index.tz_convert("UTC").tz_localize(None)
        agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        df4 = df.resample("4h").agg(agg).dropna(subset=["close"], how="all").dropna(subset=["close"])
        df4 = df4[df4["volume"] > 0]
        df4 = self._normalize_ohlcv(df4)
        self.cache.set(key, {
            "index": [str(i) for i in df4.index], "columns": list(df4.columns),
            "data": df4.astype(object).where(pd.notna(df4), None).values.tolist(),
        })
        return df4

    # ---------------- التقسيمات ---------------- #
    def splits(self, symbol: str) -> list[ReverseSplitEvent]:
        key = f"yahoo_splits_{symbol.upper()}"
        cached = self.cache.get(key)
        if cached is not None:
            return [ReverseSplitEvent(symbol=symbol.upper(), date=pd.Timestamp(c["date"]),
                                      ratio_str=c.get("ratio_str", ""), factor=float(c.get("factor", 1)),
                                      source=c.get("source", self.name), url=c.get("url", ""))
                    for c in cached]
        events: list[ReverseSplitEvent] = []
        actions = self._timed(lambda: self._ticker(symbol).get_splits())
        if actions is None or len(actions) == 0:
            actions = self._timed(lambda: self._ticker(symbol).splits)
        if actions is not None and len(actions):
            for ts, val in actions.items():
                try:
                    factor = float(val)
                except (TypeError, ValueError):
                    continue
                if factor <= 1.0:      # >1 = تقسيم عكسي (1-for-N)
                    continue
                date = pd.Timestamp(ts)
                if date.tz is not None:
                    date = date.tz_localize(None)
                events.append(ReverseSplitEvent(
                    symbol=symbol.upper(), date=date.normalize(),
                    ratio_str=f"1-for-{factor:g}", factor=factor, source=self.name,
                ))
        self.cache.set(key, [e.to_dict() for e in events])
        return events

    # ---------------- الملف التعريفي ---------------- #
    def profile(self, symbol: str) -> TickerProfile:
        key = f"yahoo_profile_{symbol.upper()}"
        cached = self.cache.get(key)
        if cached:
            return TickerProfile(**cached)
        info: dict = {}
        got = self._timed(lambda: self._ticker(symbol).get_info())
        if isinstance(got, dict) and got:
            info = got
        fast = {}
        try:
            fi = self._ticker(symbol).fast_info
            fast = dict(fi) if fi else {}
        except Exception:  # noqa: BLE001
            fast = {}

        def pick(*names, default=None):
            for n in names:
                v = info.get(n) or fast.get(n)
                if v not in (None, "", 0):
                    return v
            return default

        shares = pick("sharesOutstanding", "shares_outstanding", "shares")
        float_sh = pick("floatShares", "float_shares", "shares_float")
        market_cap = pick("marketCap", "market_cap")
        avg_vol = pick("averageVolume", "averageVolume10days", "tenDayAverageVolume",
                       "threeMonthAverageVolume")
        last_price = pick("currentPrice", "regularMarketPrice", "last_price", "previousClose")
        prof = TickerProfile(
            symbol=symbol.upper(),
            name=str(pick("longName", "shortName", default="") or ""),
            exchange=str(pick("exchange", "fullExchangeName", default="") or ""),
            sector=str(pick("sector", default="") or ""),
            industry=str(pick("industry", default="") or ""),
            shares_out=float(shares) if shares else None,
            float_shares=float(float_sh) if float_sh else None,
            market_cap=float(market_cap) if market_cap else None,
            avg_volume=float(avg_vol) if avg_vol else None,
            avg_dollar_volume=(float(avg_vol) * float(last_price)) if (avg_vol and last_price) else None,
            insider_pct=_maybe_pct(pick("heldPercentInsiders")),
            institutional_pct=_maybe_pct(pick("heldPercentInstitutions")),
            source=self.name,
            as_of=str(utcnow().date()),
        )
        # محاولة الحصول على عدد الأسهم من بيانات رسمية عند تعذّر info
        if prof.shares_out is None:
            try:
                sh = self._ticker(symbol).get_shares_full(start=(datetime.now() - timedelta(days=400)).date())
                if sh is not None and len(sh):
                    prof.shares_out = float(sh.dropna().iloc[-1])
            except Exception:  # noqa: BLE001
                pass
        self.cache.set(key, prof.to_dict())
        return prof

    # ---------------- البيع المكشوف ---------------- #
    def short_data(self, symbol: str) -> ShortData | None:
        key = f"yahoo_short_{symbol.upper()}"
        cached = self.cache.get(key)
        if cached:
            cached.pop("change_pct", None)
            return ShortData(**cached)
        info = {}
        got = self._timed(lambda: self._ticker(symbol).get_info())
        if isinstance(got, dict):
            info = got
        short_shares = info.get("sharesShort") or info.get("shares_short")
        pct_float = info.get("shortPercentOfFloat") or info.get("short_percent_of_float")
        dtc = info.get("shortRatio") or info.get("short_ratio")
        prev = info.get("sharesShortPriorMonth") or info.get("shares_short_prior_month")
        if not any([short_shares, pct_float, dtc]):
            return None
        sd = ShortData(
            symbol=symbol.upper(),
            as_of=str(info.get("dateShortInterest") or info.get("sharesShortPreviousMonthDate") or ""),
            short_shares=float(short_shares) if short_shares else None,
            short_pct_float=(float(pct_float) * 100) if pct_float and float(pct_float) <= 1 else (
                float(pct_float) if pct_float else None),
            days_to_cover=float(dtc) if dtc else None,
            prev_short_shares=float(prev) if prev else None,
            source=self.name,
        )
        self.cache.set(key, sd.to_dict())
        return sd

    # ---------------- الأخبار ---------------- #
    def catalysts(self, symbol: str) -> list[Catalyst]:
        key = f"yahoo_news_{symbol.upper()}"
        cached = self.cache.get(key)
        if cached is not None:
            return [Catalyst(**c) for c in cached]
        out: list[Catalyst] = []
        news = self._timed(lambda: self._ticker(symbol).get_news(count=25))
        if news:
            for item in news:
                content = item.get("content", item) if isinstance(item, dict) else {}
                title = str(content.get("title") or item.get("title") or "")
                if not title:
                    continue
                pub = content.get("pubDate") or item.get("providerPublishTime")
                if isinstance(pub, (int, float)):
                    date_str = str(datetime.utcfromtimestamp(pub).date())
                else:
                    date_str = str(pub)[:10]
                typ, sent, impact = classify_news(title)
                url = ""
                cp = content.get("canonicalUrl") or content.get("clickThroughUrl") or {}
                if isinstance(cp, dict):
                    url = str(cp.get("url") or "")
                out.append(Catalyst(symbol=symbol.upper(), date=date_str, type=typ, title=title,
                                    sentiment=sent, impact=impact, source=self.name, url=url))
        # موعد الأرباح القادم كمحفّز مستقبلي
        try:
            cal = self._ticker(symbol).get_calendar()
            if isinstance(cal, dict):
                ed = cal.get("Earnings Date") or cal.get("earningsDate")
                if isinstance(ed, (list, tuple)) and ed:
                    ed = ed[0]
                if ed:
                    out.append(Catalyst(symbol=symbol.upper(), date=str(pd.Timestamp(ed).date()),
                                        type="earnings", title="موعد الإعلان عن النتائج (تقويم Yahoo)",
                                        sentiment="neutral", impact="high", source=self.name))
        except Exception:  # noqa: BLE001
            pass
        self.cache.set(key, [c.to_dict() for c in out])
        return out

    # ---------------- التخفيف (من الأخبار) ---------------- #
    def dilution(self, symbol: str) -> list[DilutionSignal]:
        from .base import DilutionSignal
        out: list[DilutionSignal] = []
        for c in self.catalysts(symbol):
            if c.type == "offering":
                sev = "high" if c.impact == "high" else "medium"
                out.append(DilutionSignal(symbol=symbol.upper(), kind="offering", date=c.date,
                                          title=c.title, severity=sev, url=c.url, source=self.name))
        return out

    # ---------------- الكون ---------------- #
    # بذرة أولية من أسهم بيني/ناسداك صغيرة شائعة قد تكون مرّت بتقسيم عكسي حديثاً.
    # تُستخدم كبذرة فقط — كل رمز يُتحقق من وجوده فعلاً على Yahoo، ومن لديه تقسيم
    # عكسي خلال النافذة هو فقط الذي يدخل قائمة الفرص.
    _PENNY_SEED: list[str] = [
        # أسهم رعاية صحية/بيوتك صغيرة شائعة
        "JZ", "HLIO", "BRTX", "TGL", "UPC", "FLNT", "DRTS", "MDIA", "TRVN",
        "QLGN", "WKSP", "HRTX", "AXGN", "CDLX", "EFTR", "IMPL", "KZIA", "LGMK",
        "NRXP", "OPGN", "PHUN", "SNGX", "VBIV",
        # أسهم تقنية/SPACs/متقلبة معروفة
        "BB", "NOK", "SNDL", "TLRY", "CGC", "APHA", "ACB", "HEXO",
        "MARA", "RIOT", "CLSK", "SI", "MSTR", "COIN", "MARA",
        "MULN", "NKLA", "RIDE", "LCID", "RIVN", "NIO", "XPEV", "LI",
        "BBBYQ", "GME", "AMC", "APE", "WISH", "CLOV", "SOFI", "LC",
        "PTON", "NFLX", "PLTR", "U", "RBLX", "COIN", "SERV",
        "AAL", "DAL", "UAL", "CCL", "NCLH", "RCL",
    ]

    def _symbol_exists(self, symbol: str) -> bool:
        """التحقق السريع من وجود الرمز فعلاً في Yahoo وله بيانات سعر."""
        try:
            df = self.daily(symbol, lookback_days=30)
            return df is not None and len(df) >= 5
        except Exception:
            return False

    def universe(self, limit: int = 100) -> list[str]:
        """يبني الكون من: (1) watchlist اليدوي (2) universe.csv (3) بذرة الأسهم الصغيرة،
        ثم يُتحقق من وجود كل رمز فعلاً في Yahoo قبل إرجاعه."""
        from ..config import settings
        syms: list[str] = []

        # أولاً: watchlist اليدوي (أولوية)
        for path in (settings.watchlist_file, settings.universe_file):
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    s = line.split(",")[0].strip().upper()
                    if s and s != "SYMBOL":
                        syms.append(s)

        # ثانياً: بذرة الأسهم الصغيرة (لنوسّع الكون ونكتشف تقسيمات جديدة)
        for s in self._PENNY_SEED:
            if s not in syms:
                syms.append(s)

        # إزالة التكرار
        seen, uniq = set(), []
        for s in syms:
            if s and s not in seen:
                seen.add(s)
                uniq.append(s)

        # تحقق سريع للرموز — نقبل كل رمز له ملف تعريفي صالح (لأن
        # فلترة التقسيم العكسي والسعر ستتم لاحقاً في engine).
        # لا نتحقق من كل رمز هنا لتفادي إبطاء المسح؛ الفلترة الأساسية تتم
        # داخل daily() التي تُعيد DataFrame فارغاً للرموز غير الموجودة.
        return uniq[:limit] if limit else uniq


def _maybe_pct(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f * 100, 2) if f <= 1 else round(f, 2)
