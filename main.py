import asyncio
import requests
import datetime
import pytz
from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "بوت قناص أسهم السنتات الموقت (مع تغطية بعد الإغلاق) يعمل 24/7!"

@app.route('/test')
def test_route():
    """رابط بسيط تفتحه من الجوال عشان تبعت رسالة اختبار للقناة فورًا"""
    try:
        asyncio.run(send_telegram_message("🔔 اختبار يدوي: البوت متصل ويعمل بنجاح."))
        return "✅ تم إرسال رسالة الاختبار إلى القناة. راجع تيليجرام."
    except Exception as e:
        return f"❌ حصل خطأ أثناء الاختبار: {e}"

@app.route('/scan')
def scan_route():
    """رابط تفتحه من الجوال عشان تشغّل فحص أخبار فوري (مسح يدوي) بدل انتظار الدورة التلقائية"""
    try:
        asyncio.run(check_market_news())
        return "✅ تم تنفيذ مسح يدوي للأخبار. لو فيه خبر مطابق للشروط هيوصلك تنبيه في القناة، ولو مفيش يبقى مفيش أخبار مطابقة حاليًا."
    except Exception as e:
        return f"❌ حصل خطأ أثناء المسح: {e}"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

# --- بياناتك السرية ---
TELEGRAM_TOKEN = "8808593618:AAEUz24M2638F7Al0ZHDJndmWIX4JCDLrJE"
TELEGRAM_CHAT_ID = "-1004436952886"
FINNHUB_API_KEY = "da30k59r01qupvfb6hagda30k59r01qupvfb6hb0"

last_news_timestamp = int(datetime.datetime.now().timestamp())

CRITICAL_KEYWORDS = [
    "acquisition", "acquire", "merger", "merge", "buyout", "takeover",
    "definitive agreement", "combination", "combine", "approval",
    "approved", "partnership", "partner", "contract", "award",
    "patent", "granted", "earnings beat"
]

def is_market_hours():
    tz_ny = pytz.timezone('America/New_York')
    ny_now = datetime.datetime.now(tz_ny)
    if ny_now.weekday() > 4:
        return False
    start_time = ny_now.replace(hour=4, minute=0, second=0, microsecond=0)
    end_time = ny_now.replace(hour=18, minute=30, second=0, microsecond=0)
    return start_time <= ny_now <= end_time

async def send_telegram_message(text):
    # الرابط الصحيح لـ Telegram Bot API
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        response = await asyncio.to_thread(requests.post, url, json=payload)
        if response.status_code != 200:
            print(f"⚠️ فشل إرسال رسالة تيليجرام: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"⚠️ خطأ أثناء إرسال رسالة تيليجرام: {e}")

async def get_stock_data(ticker):
    # الرابط الصحيح لجلب بيانات السهم (quote) من Finnhub
    url = f"https://finnhub.io/api/v1/quote?symbol={ticker}&token={FINNHUB_API_KEY}"
    try:
        response = await asyncio.to_thread(requests.get, url)
        if response.status_code == 200:
            data = response.json()
            current_price = data.get("c", 0)
            previous_close = data.get("pc", 0)
            volume = data.get("v", 0)
            price_change_percent = 0.0
            if previous_close > 0:
                price_change_percent = ((current_price - previous_close) / previous_close) * 100
            return current_price, price_change_percent, volume
        else:
            print(f"⚠️ فشل جلب بيانات السهم {ticker}: {response.status_code}")
    except Exception as e:
        print(f"⚠️ خطأ أثناء جلب بيانات السهم {ticker}: {e}")
    return 0, 0, 0

def format_volume(volume):
    if volume >= 1_000_000:
        return f"{volume / 1_000_000:.2f}M (مليون)"
    elif volume >= 1_000:
        return f"{volume / 1_000:.1f}K (ألف)"
    return str(volume)

def contains_trigger_keyword(title):
    title_lower = title.lower()
    dismiss_words = ["technical analysis", "price target", "why it dropped", "why it rose", "stock alert"]
    if any(dismiss in title_lower for dismiss in dismiss_words):
        return False
    return any(keyword in title_lower for keyword in CRITICAL_KEYWORDS)

async def check_market_news():
    global last_news_timestamp
    # الرابط الصحيح لجلب الأخبار العامة من Finnhub
    url = f"https://finnhub.io/api/v1/news?category=general&token={FINNHUB_API_KEY}"
    try:
        response = await asyncio.to_thread(requests.get, url)
        if response.status_code == 200:
            news_list = response.json()
            for news in reversed(news_list[:15]):
                news_time = news.get("datetime", 0)
                if news_time > last_news_timestamp:
                    title = news.get("headline", "")
                    related_symbols = news.get("relatedSymbols", [])
                    if related_symbols and contains_trigger_keyword(title):
                        ticker = related_symbols[0] if isinstance(related_symbols, list) else related_symbols
                        current_price, price_change_percent, current_volume = await get_stock_data(ticker)
                        if 0.01 <= current_price <= 5.00:
                            news_url = news.get("url", "")
                            icon = "🚨"
                            if any(x in title.lower() for x in ["acquisition", "acquire", "merger", "buyout", "takeover"]):
                                icon = "💰🔥 [فرصة استحواذ/اندماج]"
                            readable_volume = format_volume(current_volume)
                            change_icon = "📈 🟢" if price_change_percent >= 0 else "📉 🔴"
                            message = (
                                f"{icon} *قنّاص محفزات أسهم السنتات (< $5)*\n\n"
                                f"📈 *السهم:* `{ticker}`\n"
                                f"💰 *السعر الحالي:* ${current_price:.2f}\n"
                                f"{change_icon} *التغير اليومي:* `{price_change_percent:.2f}%`\n"
                                f"📊 *حجم التداول اليومي:* `{readable_volume}`\n"
                                f"📢 *العنوان:* {title}\n\n"
                                f"🔗 [رابط الخبر والتفاصيل]({news_url})"
                            )
                            await send_telegram_message(message)
                            await asyncio.sleep(1)
                    last_news_timestamp = news_time
        else:
            print(f"⚠️ فشل جلب الأخبار: {response.status_code}")
    except Exception as e:
        print(f"⚠️ خطأ أثناء جلب الأخبار: {e}")

async def send_startup_message():
    """رسالة اختبار تُرسل فور تشغيل البوت للتأكد من أن الاتصال بتيليجرام يعمل"""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    await send_telegram_message(f"✅ البوت اشتغل بنجاح وتم الاتصال بتيليجرام بنجاح.\n🕒 {now}")

async def bot_loop():
    print("🤖 البوت الذكي يعمل وفق التوقيت الموسع الجديد...")
    await send_startup_message()
    while True:
        if is_market_hours():
            await check_market_news()
            await asyncio.sleep(5)
        else:
            await asyncio.sleep(600)

if __name__ == "__main__":
    Thread(target=run_flask).start()
    asyncio.run(bot_loop())
