"""مزوّد الأخبار — تدفقات RSS مجانية + تصنيف اتجاهي عربي.

المصادر (كلها مجانية بدون مفاتيح):
  • Yahoo Finance RSS لكل رمز
  • Google News RSS (بحث بالرمز)
  • Nasdaq RSS (عند توفّره)

تُصنّف العناوين إلى: إيجابي / سلبي / محايد + الأثر (عالٍ/متوسط/منخفض)،
وتُستخرج المحفزات المستقبلية (طرح أسهم، أرباح، امتثال، نتائج دراسات).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

from .base import BaseProvider, Catalyst, ProviderStatus
from ..config import settings
from .yahoo import classify_news

FEEDS = {
    "yahoo": "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US",
    "google": "https://news.google.com/rss/search?q={symbol}+stock&hl=en-US&gl=US&ceid=US:en",
    "nasdaq": "https://www.nasdaq.com/feed/rssoutbound?symbol={symbol}",
}

FUTURE_KEYS = [
    ("offering", ["offering", "registered direct", "private placement", "pricing of"]),
    ("earnings", ["earnings", "results", "quarter", "fiscal"]),
    ("compliance", ["compliance", "deficiency", "delist", "minimum bid", "notice"]),
    ("clinical", ["data", "trial", "readout", "fda", "pdufa"]),
]


class NewsProvider(BaseProvider):
    name = "news-rss"
    kind = "live"

    def __init__(self, cache=None):
        super().__init__(cache)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": settings.sec_user_agent})

    def _feed(self, url: str) -> list[dict]:
        try:
            r = requests.get(url, headers={"User-Agent": settings.sec_user_agent},
                             timeout=settings.http_timeout)
            if r.status_code >= 400:
                return []
            root = ET.fromstring(r.content)
        except Exception:  # noqa: BLE001
            return []
        items: list[dict] = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub = (item.findtext("pubDate") or "").strip()
            desc = (item.findtext("description") or "").strip()
            if not title:
                continue
            items.append({"title": title, "link": link, "pub": pub, "desc": desc})
        return items

    def catalysts(self, symbol: str) -> list[Catalyst]:
        symbol = symbol.upper()
        key = f"news_{symbol}"
        cached = self.cache.get(key)
        if cached is not None:
            return [Catalyst(**c) for c in cached]
        out: list[Catalyst] = []
        for src, tpl in FEEDS.items():
            for it in self._feed(tpl.format(symbol=symbol))[:20]:
                typ, sent, impact = classify_news(it["title"])
                date_str = _parse_rss_date(it["pub"]) or str(datetime.now(timezone.utc).date())
                out.append(Catalyst(symbol=symbol, date=date_str, type=typ, title=it["title"],
                                    sentiment=sent, impact=impact, source=f"rss:{src}",
                                    url=it["link"]))
        # إزالة التكرار بالعنوان
        seen, uniq = set(), []
        for c in out:
            k = c.title[:90].lower()
            if k in seen:
                continue
            seen.add(k)
            uniq.append(c)
        if uniq:
            self.status = ProviderStatus(self.name, True, f"{len(uniq)} عنوان")
        self.cache.set(key, [c.to_dict() for c in uniq])
        return uniq

    def dilution(self, symbol: str) -> list:
        from .base import DilutionSignal
        out = []
        for c in self.catalysts(symbol):
            if c.type == "offering":
                out.append(DilutionSignal(symbol=symbol.upper(), kind="news-offering", date=c.date,
                                          title=c.title, severity="high" if c.impact == "high" else "medium",
                                          url=c.url, source=c.source))
        return out

    def profile(self, symbol: str):
        from .base import TickerProfile
        return TickerProfile(symbol=symbol.upper(), source=self.name)

    def splits(self, symbol: str):
        return []

    def daily(self, symbol: str, lookback_days: int = 260):
        return self.empty_df()

    def intraday_4h(self, symbol: str, lookback_days: int = 90):
        return self.empty_df()

    def universe(self, limit: int = 100):
        return []


def _parse_rss_date(raw: str) -> str | None:
    if not raw:
        return None
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return str(datetime.strptime(raw, fmt).date())
        except ValueError:
            continue
    return None
