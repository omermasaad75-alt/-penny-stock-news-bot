import time
import re
import json
import os
import requests
import feedparser
from datetime import datetime, timezone

# ============ الإعدادات ============
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8808593618:AAEUz24M2638F7Al0ZHDJndmWIX4JCDLrJE")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1004436952886")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "da30k59r01qupvfb6hagda30k59r01qupvfb6hb0")

POLL_INTERVAL_SECONDS = 8
SEEN_IDS_FILE = "seen_news_ids.json"

# نطاق السعر المستهدف (بني ستوك)
PRICE_MIN = 0.10
PRICE_MAX = 5.00
PRICE_CACHE_TTL = 60
_price_cache = {}

# كلمات مفتاحية محفزة
POSITIVE_KEYWORDS = [
    "fda approval", "fda clearance", "breakthrough therapy", "phase 3 results",
    "positive results", "granted patent", "patent granted", "acquisition",
    "to be acquired", "merger agreement", "strategic partnership",
    "definitive agreement", "record revenue", "raises guidance",
    "contract win", "awarded contract", "government contract",
    "nasdaq uplisting", "uplisting to nasdaq", "share buyback",
    "positive topline", "meets primary endpoint", "letter of intent",
    "signs agreement", "expands partnership", "doe grant", "nih grant",
]

NEGATIVE_FILTER = [
    "reverse split", "going concern", "delisting", "bankruptcy",
    "recall", "lawsuit", "sec investigation", "clinical hold",
]

# ============ أدوات مساعدة ============

def load_seen_ids():
    if os.path.exists(SEEN_IDS_FILE):
        try:
            with open(SEEN_IDS_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_seen_ids(seen_ids):
    trimmed = list(seen_ids)[-5000:]
    with open(SEEN_IDS_FILE, "w") as f:
        json.dump(trimmed, f)

def classify_headline(headline: str):
    text = headline.lower()
    if any(neg in text for neg in NEGATIVE_FILTER):
        return None
    for kw in POSITIVE_KEYWORDS:
        if kw in text:
            return kw
    return None

def send_telegram_alert(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            print(f"[تحذير] فشل إرسال تليجرام: {r.text}")
    except Exception as e:
        print(f"[خطأ] استثناء أثناء إرسال تليجرام: {e}")

def now_str():
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

def get_stock_stats(symbol: str):
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
        r.raise_for_status()
        q = r.json()
        stats["price"] = q.get("c")
        stats["change_pct"] = q.get("dp")
    except Exception as e:
        print(f"[خطأ] جلب سعر {symbol}: {e}")

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
        r.raise_for_status()
        c = r.json()
        if c.get("s") == "ok" and c.get("v"):
            stats["volume"] = sum(c["v"])
    except Exception as e:
        print(f"[تنبيه] تعذر جلب الفوليوم لـ {symbol}: {e}")

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

def is_price_in_range(symbol: str):
    stats = get_stock_stats(symbol)
    if not stats or stats["price"] is None:
        return None
    in_range = PRICE_MIN <= stats["price"] <= PRICE_MAX
    return in_range, stats

# ============ مصادر الأخبار ============

def check_finnhub_news(seen_ids):
    url = "https://finnhub.io/api/v1/news"
    params = {"category": "general", "token": FINNHUB_API_KEY}
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        items = r.json()
    except Exception as e:
        print(f"[خطأ] Finnhub general news: {e}")
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
        if not symbols:
            continue

        link = item.get("url", "")
        for symbol in symbols:
            result = is_price_in_range(symbol)
            if result is None:
                continue
            in_range, stats = result
            if not in_range:
                continue

            price = stats["price"]
            change = stats["change_pct"]
            volume = stats["volume"]
            change_str = f"{change:.2f}%" if change is not None else "غير متاح"
            
            msg = (
                f"🚨 <b>خبر محفّز محتمل</b>\n"
                f"⏰ {now_str()}\n"
                f"📌 السهم: <b>{symbol}</b>\n"
                f"💰 السعر: ${price:.2f} | 📈 التغيير: {change_str}\n"
                f"📊 الحجم اليومي: {format_volume(volume)}\n"
                f"🔑 الكلمة المفتاحية: {matched_kw}\n"
                f"📰 {headline}\n"
                f"🔗 {link}"
            )
            send_telegram_alert(msg)
            print(f"[تنبيه] {symbol} (${price:.2f}, {change_str}): {headline}")

WATCHLIST = []

def check_finnhub_company_news(seen_ids):
    if not WATCHLIST:
        return
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for symbol in WATCHLIST:
        url = "https://finnhub.io/api/v1/company-news"
        params = {
            "symbol": symbol,
            "from": today,
            "to": today,
            "token": FINNHUB_API_KEY,
        }
        try:
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
            items = r.json()
        except Exception as e:
            print(f"[خطأ] Finnhub company news لـ {symbol}: {e}")
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
            if not in_range:
                continue

            price = stats["price"]
            change = stats["change_pct"]
            volume = stats["volume"]
            change_str = f"{change:.2f}%" if change is not None else "غير متاح"
            
            link = item.get("url", "")
            msg = (
                f"🚨 <b>خبر محفّز - سهم بالمراقبة</b>\n"
                f"⏰ {now_str()}\n"
                f"📌 السهم: <b>{symbol}</b>\n"
                f"💰 السعر: ${price:.2f} | 📈 التغيير: {change_str}\n"
                f"📊 الحجم اليومي: {format_volume(volume)}\n"
                f"🔑 الكلمة المفتاحية: {matched_kw}\n"
                f"📰 {headline}\n"
                f"🔗 {link}"
            )
            send_telegram_alert(msg)
            print(f"[تنبيه] {symbol} (${price:.2f}, {change_str}): {headline}")

GLOBENEWSWIRE_RSS = "https://www.globenewswire.com/RssFeed/subjectcode/9-Press%20Releases/feedTitle/GlobeNewswire%20-%20News%20Releases"

TICKER_PATTERN = re.compile(
    r"\((?:NASDAQ|NYSE(?:\s+American)?|OTC(?:QB|QX|MKTS)?|AMEX)\s*[:\-]\s*([A-Z]{1,5})\)",
    re.IGNORECASE,
)

def extract_ticker(text: str):
    match = TICKER_PATTERN.search(text or "")
    if match:
        return match.group(1).upper()
    return None

def check_globenewswire(seen_ids):
    try:
        feed = feedparser.parse(GLOBENEWSWIRE_RSS)
    except Exception as e:
        print(f"[خطأ] GlobeNewswire RSS: {e}")
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
        if not in_range:
            continue

        price = stats["price"]
        change = stats["change_pct"]
        volume = stats["volume"]
        change_str = f"{change:.2f}%" if change is not None else "غير متاح"
        
        link = entry.get("link", "")
        msg = (
            f"🚨 <b>خبر محفّز - GlobeNewswire</b>\n"
            f"⏰ {now_str()}\n"
            f"📌 السهم: <b>{symbol}</b>\n"
            f"💰 السعر: ${price:.2f} | 📈 التغيير: {change_str}\n"
            f"📊 الحجم اليومي: {format_volume(volume)}\n"
            f"🔑 الكلمة المفتاحية: {matched_kw}\n"
            f"📰 {headline}\n"
            f"🔗 {link}"
        )
        send_telegram_alert(msg)
        print(f"[تنبيه] GNW {symbol} (${price:.2f}, {change_str}): {headline}")

SEC_HEADERS = {"User-Agent": "PennyStockAlertBot contact@example.com"}
SEC_CURRENT_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&company=&dateb=&owner=include&count=40&output=atom"

def check_sec_filings(seen_ids):
    try:
        feed = feedparser.parse(SEC_CURRENT_URL, request_headers=SEC_HEADERS)
    except Exception as e:
        print(f"[خطأ] SEC EDGAR: {e}")
        return

    for entry in feed.entries:
        news_id = f"sec-{entry.get('id', entry.get('link', ''))}"
        if news_id in seen_ids:
            continue
        seen_ids.add(news_id)

        title = entry.get("title", "")
        link = entry.get("link", "")
        msg = (
            f"📋 <b>إيداع SEC جديد (8-K)</b> — السعر غير متحقق منه\n"
            f"⏰ {now_str()}\n"
            f"📰 {title}\n"
            f"🔗 {link}"
        )
        send_telegram_alert(msg)
        print(f"[تنبيه SEC] {title}")

# ============ الحلقة الرئيسية ============

def main():
    print("=== بدء تشغيل بوت تنبيهات الأخبار ===")
    if "ضع_" in TELEGRAM_BOT_TOKEN or "ضع_" in TELEGRAM_CHAT_ID or "ضع_" in FINNHUB_API_KEY:
        print("⚠️  لازم تعبي البيانات الأول.")
        return

    seen_ids = load_seen_ids()
    send_telegram_alert("✅ بوت تنبيهات الأخبار اشتغل وبيراقب دلوقتي...")

    loop_count = 0
    while True:
        try:
            check_finnhub_news(seen_ids)
            check_finnhub_company_news(seen_ids)
            check_globenewswire(seen_ids)

            if loop_count % 2 == 0:
                check_sec_filings(seen_ids)

            save_seen_ids(seen_ids)
        except Exception as e:
            print(f"[خطأ عام في الحلقة]: {e}")

        loop_count += 1
        time.sleep(POLL_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()
