"""مزوّد SEC EDGAR — بيانات رسمية مجانية (تقسيمات عكسية، طرح أسهم، إفصاحات).

يستخدم واجهات SEC العامة (بدون مفاتيح):
  • https://www.sec.gov/files/company_tickers.json      ← خريطة الرمز ⇒ CIK
  • https://data.sec.gov/submissions/CIK##########.json ← كل الإفصاحات
"""
from __future__ import annotations

import time

import pandas as pd
import requests

from .base import BaseProvider, Catalyst, DilutionSignal, ProviderStatus, ReverseSplitEvent
from ..config import settings

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

# نموذج الإفصاح ⇒ (النوع، درجة الخطورة)
DILUTION_FORMS: dict[str, tuple[str, str]] = {
    "424B5": ("424B5", "high"),     # طرح فعلي بمنشور
    "424B3": ("424B3", "medium"),
    "S-1": ("S-1", "medium"),       # تسجيل إصدار جديد
    "S-1/A": ("S-1", "medium"),
    "S-3": ("S-3", "medium"),
    "S-3/A": ("S-3", "medium"),
    "S-3MEF": ("S-3", "medium"),
    "EFFECT": ("effectiveness", "medium"),
    "424A": ("424A", "high"),
}
NEGATIVE_FORMS = {
    "NT 10-Q": ("delay", "high"),
    "NT 10-K": ("delay", "high"),
    "8-K": ("8-K", "medium"),
}


class SECProvider(BaseProvider):
    name = "sec-edgar"
    kind = "live"

    def __init__(self, cache=None, user_agent: str | None = None):
        super().__init__(cache)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent or settings.sec_user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Host": "data.sec.gov",
        })
        self._ticker_map: dict[str, int] | None = None

    # ---------------------------------------------------------------- #
    def _get_json(self, url: str, host: str | None = None, timeout: int | None = None) -> dict | None:
        headers = {"User-Agent": settings.sec_user_agent}
        if host:
            headers["Host"] = host
        t0 = time.time()
        try:
            r = requests.get(url, headers=headers, timeout=timeout or settings.http_timeout)
            if r.status_code >= 400:
                self.status = ProviderStatus(self.name, False, f"HTTP {r.status_code}")
                return None
            self.status = ProviderStatus(self.name, True, latency_ms=int((time.time() - t0) * 1000))
            return r.json()
        except Exception as exc:  # noqa: BLE001
            self.status = ProviderStatus(self.name, False, str(exc)[:180])
            return None

    def ticker_map(self) -> dict[str, int]:
        if self._ticker_map is not None:
            return self._ticker_map
        cached = self.cache.get("sec_ticker_map")
        if cached:
            self._ticker_map = {k.upper(): int(v) for k, v in cached.items()}
            return self._ticker_map
        data = self._get_json(TICKER_MAP_URL, host="www.sec.gov")
        out: dict[str, int] = {}
        if isinstance(data, dict):
            for row in data.values():
                try:
                    out[str(row["ticker"]).upper()] = int(row["cik_str"])
                except (KeyError, TypeError, ValueError):
                    continue
        if out:
            self.cache.set("sec_ticker_map", out)
        self._ticker_map = out
        return out

    def cik_for(self, symbol: str) -> int | None:
        return self.ticker_map().get(symbol.upper())

    def submissions(self, symbol: str) -> dict | None:
        cik = self.cik_for(symbol)
        if not cik:
            return None
        key = f"sec_subs_{cik}"
        cached = self.cache.get(key)
        if cached:
            return cached
        data = self._get_json(SUBMISSIONS_URL.format(cik=cik))
        if data:
            slim = {
                "cik": cik,
                "name": data.get("name"),
                "tickers": data.get("tickers"),
                "recent": data.get("filings", {}).get("recent", {}),
            }
            self.cache.set(key, slim)
            return slim
        return None

    def _recent_filings(self, symbol: str, limit: int = 200) -> list[dict]:
        subs = self.submissions(symbol)
        if not subs:
            return []
        recent = subs.get("recent") or {}
        forms = recent.get("form") or []
        dates = recent.get("filingDate") or []
        accs = recent.get("accessionNumber") or []
        docs = recent.get("primaryDocument") or []
        descs = recent.get("primaryDocDescription") or []
        items = recent.get("items") or [""] * len(forms)
        cik = subs.get("cik")
        out: list[dict] = []
        for i in range(min(limit, len(forms))):
            acc = str(accs[i]).replace("-", "") if i < len(accs) else ""
            url = (f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{docs[i]}"
                   if acc and i < len(docs) else f"https://www.sec.gov/cgi-bin/browse-edgar"
                   f"?action=getcompany&CIK={cik:010d}&type={forms[i]}")
            out.append({
                "form": str(forms[i]), "date": str(dates[i]),
                "url": url, "description": str(descs[i]) if i < len(descs) else "",
                "items": str(items[i]) if i < len(items) else "",
            })
        return out

    # ---------------- التقسيمات العكسية ---------------- #
    def splits(self, symbol: str) -> list[ReverseSplitEvent]:
        """استخراج التقسيمات العكسية من الإفصاحات (8-K بنود 3.03 / DEF 14A / 25-NSE)."""
        key = f"sec_splits_{symbol.upper()}"
        cached = self.cache.get(key)
        if cached is not None:
            return [ReverseSplitEvent(symbol=symbol.upper(), date=pd.Timestamp(c["date"]),
                                      ratio_str=c.get("ratio_str", ""), factor=float(c.get("factor", 1)),
                                      source=c.get("source", self.name), url=c.get("url", ""))
                    for c in cached]
        events: list[ReverseSplitEvent] = []
        rs_forms = {"8-K", "8-K/A", "DEF 14A", "PRE 14A", "25-NSE", "S-1", "10-Q", "10-K"}
        for f in self._recent_filings(symbol, limit=300):
            if f["form"] not in rs_forms:
                continue
            items = f.get("items") or ""
            desc = (f.get("description") or "").lower()
            if "3.03" not in items and "reverse" not in desc and "5.03" not in items:
                continue
            ratio, factor = _parse_ratio(f"{desc} {items}")
            if factor <= 1:
                continue  # لا نفترض نسبة من عنوان/بنود إفصاح لا يذكرها
            events.append(ReverseSplitEvent(
                symbol=symbol.upper(), date=pd.Timestamp(f["date"]), ratio_str=ratio,
                factor=factor, source=self.name, url=f["url"],
            ))
        self.cache.set(key, [e.to_dict() for e in events])
        return events

    # ---------------- التخفيف ---------------- #
    def dilution(self, symbol: str) -> list[DilutionSignal]:
        key = f"sec_dilution_{symbol.upper()}"
        cached = self.cache.get(key)
        if cached is not None:
            return [DilutionSignal(**c) for c in cached]
        out: list[DilutionSignal] = []
        for f in self._recent_filings(symbol, limit=120):
            form = f["form"]
            if form in DILUTION_FORMS:
                kind, sev = DILUTION_FORMS[form]
                # 8-K المتعلق بالتمويل يكون بند 1.01 أو 3.02
                out.append(DilutionSignal(
                    symbol=symbol.upper(), kind=kind, date=f["date"], severity=sev,
                    title=f"إفصاح {form}" + (f" — {f['description']}" if f["description"] else ""),
                    url=f["url"], source=self.name,
                ))
            elif form in NEGATIVE_FORMS and form.startswith("NT"):
                kind, sev = NEGATIVE_FORMS[form]
                out.append(DilutionSignal(
                    symbol=symbol.upper(), kind=kind, date=f["date"], severity=sev,
                    title=f"تأخير في تقديم {form} (مخاطر امتثال)", url=f["url"], source=self.name,
                ))
            elif form == "8-K" and any(x in (f.get("items") or "") for x in ("1.01", "3.02")):
                out.append(DilutionSignal(
                    symbol=symbol.upper(), kind="8-K", date=f["date"], severity="medium",
                    title=f"8-K بند {f.get('items')} — اتفاقية/إصدار أسهم", url=f["url"], source=self.name,
                ))
        self.cache.set(key, [d.to_dict() for d in out])
        return out

    # ---------------- المحفزات ---------------- #
    def catalysts(self, symbol: str) -> list[Catalyst]:
        out: list[Catalyst] = []
        for f in self._recent_filings(symbol, limit=40):
            form = f["form"]
            sentiment, impact, typ = "neutral", "low", "other"
            if form in {"424B5", "S-1", "S-3", "424B3", "424A"}:
                sentiment, impact, typ = "negative", "high", "offering"
            elif form in {"8-K", "8-K/A"}:
                sentiment, impact, typ = "neutral", "medium", "other"
            elif form in {"10-Q", "10-K"}:
                sentiment, impact, typ = "neutral", "medium", "earnings"
            elif form in {"SC 13D", "SC 13G", "SC 13D/A", "SC 13G/A"}:
                sentiment, impact, typ = "positive", "medium", "other"
            elif form.startswith("4"):
                continue
            out.append(Catalyst(symbol=symbol.upper(), date=f["date"], type=typ,
                                title=f"إفصاح SEC: {form}" + (f" — {f['description']}" if f["description"] else ""),
                                sentiment=sentiment, impact=impact, source=self.name, url=f["url"]))
        return out

    def profile(self, symbol: str):
        from .base import TickerProfile
        subs = self.submissions(symbol)
        if not subs:
            return TickerProfile(symbol=symbol.upper(), source=self.name)
        return TickerProfile(symbol=symbol.upper(), name=str(subs.get("name") or ""),
                             source=self.name)

    def universe(self, limit: int = 100) -> list[str]:
        return []


def _parse_ratio(text: str) -> tuple[str, float]:
    """استخراج نسبة التقسيم العكسي من نص إفصاح. مثال: '1-for-20' ⇒ (1-for-20, 20)."""
    import re
    t = (text or "").lower()
    patterns = [
        r"1[\s\-]*(?:for|:) *(\d{1,6})",           # 1-for-20 — بلا سقف عملي
        r"one[\s\-]*(?:for|:) *(\d{1,6})",
        r"reverse (?:stock )?split[^\d]{0,30}(\d{1,6})[\s\-]*(?:to|for|:) *1",
    ]
    for pat in patterns:
        m = re.search(pat, t)
        if m:
            try:
                f = float(m.group(1))
                if f > 1:
                    return f"1-for-{f:g}", f
            except ValueError:
                continue
    return "", 0.0
