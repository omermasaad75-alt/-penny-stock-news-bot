import time
import re
import json
import os
import requests
import feedparser
from datetime import datetime, timezone

TELEGRAM_BOT_TOKEN = "8808593618:AAEUz24M2638F7Al0ZHDJndmWIX4JCDLrJE"
TELEGRAM_CHAT_ID = "-1004436952886"
FINNHUB_API_KEY = "da30k59r01qupvfb6hagda30k59r01qupvfb6hb0"

POLL_INTERVAL_SECONDS = 8
SEEN_IDS_FILE = "seen_news_ids.json"
PRICE_MIN = 0.10
PRICE_MAX = 5.00
PRICE_CACHE_TTL = 60
_price_cache = {}

KEYWORD_TRANSLATIONS = {
    "fda approval": "موافقة FDA",
    "fda clearance": "تخليص FDA",
    "breakthrough therapy": "علاج اختراق",
    "phase 3 results": "نتائج المرحلة 3",
    "positive results": "نتائج إيجابية",
    "granted patent": "براءة اختراع",
    "patent granted": "براءة اختراع",
    "acquisition": "استحواذ",
    "to be acquired": "تحت الاستحواذ",
    "merger agreement": "اندماج",
    "strategic partnership": "شراكة استراتيجية",
    "definitive agreement": "اتفاق نهائي",
    "record revenue": "إيرادات قياسية",
    "raises guidance": "رفع التوقعات",
    "contract win": "فوز بعقد",
    "awarded contract": "حصول على عقد",
    "government contract": "عقد حكومي",
    "nasdaq uplisting": "صعود ناسداك",
    "uplisting to nasdaq": "الانتقال لناسداك",
    "share buyback": "إعادة شراء أسهم",
    "positive topline": "إيرادات إيجابية",
    "meets primary endpoint": "تحقيق الهدف",
    "letter of intent": "نية الشراء",
    "signs agreement": "توقيع اتفاق",
    "expands partnership": "توسيع الشراكة",
    "doe grant": "منحة وزارة الطاقة",
    "nih grant": "منحة المعاهد الصحية",
}

POSITIVE_KEYWORDS = list(KEYWORD_TRANSLATIONS.keys())

NEGATIVE_FILTER = [
    "reverse split", "going concern", "delisting", "bankruptcy",
    "recall", "lawsuit", "sec investigation", "clinical hold",
]

def load_seen_ids():
    if os.path.exists(SEEN_IDS_FILE):
        try:
            with open(SEEN_IDS_FILE, "r") as f:
                return set(json.load(f))
        except:
            return set()
    return set()

def save_seen_ids(seen_ids):
    trimmed = list(seen_ids)[-5000:]
    with open(SEEN_IDS_FILE, "w") as f:
        json.dump(trimmed, f)

def classify_headline(headline):
    text = headline.lower()
    if any(neg in text for neg in NEGATIVE_FILTER):
        return None
    for kw in POSITIVE_KEYWORDS:
        if kw in text:
            return kw
    return None

def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"خطأ: {e}")

def get_stock_stats(symbol):
    if not symbol:
        return None

    cached = _price_cache.get(symbol)
    if cached and (time.time() - cached[0]) < PRICE_CACHE_TTL:
        return cached[1]

    stats = {"price": None, "change_pct": None, "volume": None}

    try:
        r = requests.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": symbol, "token": FINNHUB_API_KEY},
            timeout=8,
        )
        q = r.json()
        stats["price"] = q.get("c")
        stats["change_pct"] = q.get("dp")
    except Exception as e:
        print(f"خطأ: {e}")

    try:
        today = datetime.now(timezone.utc)
        start_ts = int(today.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        end_ts = int(time.time())
        r = requests.get(
            "https://finnhub.io/api/v1/stock/candle",
            params={
                "symbol": symbol,
                "resolution": "5",
                "from": start_ts,
                "to": end_ts,
                "token": FINNHUB_API_KEY,
            },
            timeout=8,
        )
        c = r.json()
        if c.get("s") == "ok" and c.get("v"):
            stats["volume"] = sum(c["v"])
    except:
        pass

    if stats["price"] is not None:
        _price_cache[symbol] = (time.time(), stats)
    return stats

def format_volume(v):
    if v is None:
        return "غير متاح"
    if v >= 1_000_000:
        return f"{v/1_000_000:.2f}M"
    if v >= 1_000:
        return f"{v/1_000:.0f}K"
    return str(v)

def is_price_in_range(symbol):
    stats = get_stock_stats(symbol)
    if not stats or stats["price"] is None:
        return None
    in_range = PRICE_MIN <= stats["price"] <= PRICE_MAX
    return in_range, stats if in_range else None

def create_alert(symbol, stats, keyword, headline, link):
    price = stats["price"]
    change = stats["change_pct"]
    volume = stats["volume"]
    
    news_type = KEYWORD_TRANSLATIONS.get(keyword, keyword)
    change_str = f"{change:.1f}%" if change else "غير متاح"
    volume_str = format_volume(volume)
    
    liquidity = int((volume * price) / 1_000_000) if volume and price else 0
    liquidity_str = f"{liquidity}M" if liquidity >= 1 else f"{int(volume/1000) if volume else 0}K"
    
    msg = (
        f"<b>━━━━━━━━━━━━━━━━━━━━━</b>\n"
        f"<b>📌 اسم السهم:</b> {symbol}\n"
        f"<b>💵 السعر الحالي:</b> ${price:.2f}\n"
        f"<b>📊 نوع الخبر:</b> {news_type}\n"
        f"<b>📈 الصعود الآن:</b> {change_str}\n"
        f"<b>📦 حجم التداول:</b> {volume_str}\n"
        f"<b>💧 حجم السيولة:</b> {liquidity_str}\n"
        f"<b>━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        f"<b>📰 الخبر:</b>\n{headline}\n\n"
        f"🔗 {link}"
    )
    return msg

def check_finnhub_news(seen_ids):
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/news",
            params={"category": "general", "token": FINNHUB_API_KEY},
            timeout=10,
        )
        items = r.json()
    except:
        return

    for item in items:
        news_id = f"finnhub-{item.get('id')}"
        if news_id in seen_ids:
            continue
        seen_ids.add(news_id)

        headline = item.get("headline", "")
        matched_kw = classify_headline(headline)
        if not matched_kw:
            continue

        related_raw = item.get("related", "") or ""
        symbols = [s.strip() for s in related_raw.split(",") if s.strip()]
        
        link = item.get("url", "")
        for symbol in symbols:
            result = is_price_in_range(symbol)
            if result is None:
                continue
            in_range, stats = result
            if not in_range or not stats:
                continue

            msg = create_alert(symbol, stats, matched_kw, headline, link)
            send_telegram_alert(msg)
            print(f"[تنبيه] {symbol}: {headline}")

WATCHLIST = []

def check_finnhub_company_news(seen_ids):
    if not WATCHLIST:
        return
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for symbol in WATCHLIST:
        try:
            r = requests.get(
                "https://finnhub.io/api/v1/company-news",
                params={"symbol": symbol, "from": today, "to": today, "token": FINNHUB_API_KEY},
                timeout=10,
            )
            items = r.json()
        except:
            continue

        for item in items:
            news_id = f"finnhub-company-{item.get('id')}"
            if news_id in seen_ids:
                continue
            seen_ids.add(news_id)

            headline = item.get("headline", "")
            matched_kw = classify_headline(headline)
            if not matched_kw:
                continue

            result = is_price_in_range(symbol)
            if result is None:
                continue
            in_range, stats = result
            if not in_range or not stats:
                continue

            link = item.get("url", "")
            msg = create_alert(symbol, stats, matched_kw, headline, link)
            send_telegram_alert(msg)

GLOBENEWSWIRE_RSS = "https://www.globenewswire.com/RssFeed/subjectcode/9-Press%20Releases/feedTitle/GlobeNewswire%20-%20News%20Releases"
TICKER_PATTERN = re.compile(r"\((?:NASDAQ|NYSE|OTC|AMEX)\s*[:\-]\s*([A-Z]{1,5})\)", re.IGNORECASE)

def extract_ticker(text):
    match = TICKER_PATTERN.search(text or "")
    return match.group(1).upper() if match else None

def check_globenewswire(seen_ids):
    try:
        feed = feedparser.parse(GLOBENEWSWIRE_RSS)
    except:
        return

    for entry in feed.entries:
        news_id = f"gnw-{entry.get('id', entry.get('link', ''))}"
        if news_id in seen_ids:
            continue
        seen_ids.add(news_id)

        headline = entry.get("title", "")
        summary = entry.get("summary", "")
        matched_kw = classify_headline(headline)
        if not matched_kw:
            continue

        symbol = extract_ticker(headline) or extract_ticker(summary)
        if not symbol:
            continue

        result = is_price_in_range(symbol)
        if result is None:
            continue
        in_range, stats = result
        if not in_range or not stats:
            continue

        link = entry.get("link", "")
        msg = create_alert(symbol, stats, matched_kw, headline, link)
        send_telegram_alert(msg)

def main():
    print("بوت اخبار الاسهم شغال...")
    seen_ids = load_seen_ids()
    send_telegram_alert("✅ البوت شغال وبيراقب!")

    while True:
        try:
            check_finnhub_news(seen_ids)
            check_finnhub_company_news(seen_ids)
            check_globenewswire(seen_ids)
            save_seen_ids(seen_ids)
        except Exception as e:
            print(f"خطأ: {e}")

        time.sleep(POLL_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()
