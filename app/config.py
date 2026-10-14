"""إعدادات النظام العامة + معاملات الاستراتيجية.

Configuration hub for the RS-Reversal Terminal.
All strategy thresholds are declared in one place (`StrategyParams`) so the
rule-set from the strategy sheet can be audited / tuned without touching logic.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
REPORTS_DIR = BASE_DIR / "reports" / "output"
WEB_DIR = BASE_DIR / "web"
CONFIG_DIR = BASE_DIR / "config"

for _d in (DATA_DIR, CACHE_DIR, REPORTS_DIR, CONFIG_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _env(key: str, default: Any, cast=str):
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        if cast is bool:
            return str(raw).strip().lower() in {"1", "true", "yes", "on"}
        return cast(raw)
    except (TypeError, ValueError):
        return default


def _load_dotenv() -> None:
    """تحميل .env بدون إلزام مكتبة خارجية."""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


_load_dotenv()


# --------------------------------------------------------------------------- #
#  إعدادات عامة
# --------------------------------------------------------------------------- #
@dataclass
class Settings:
    app_name: str = "RS Reversal Terminal"
    app_name_ar: str = "منصة رصد التقسيم العكسي والانعكاس"
    version: str = "1.0.0"

    # live | demo  (auto يُعامل كـ live — لا نسمح بالتراجع الصامت للديمو)
    data_mode: str = _env("RSS_DATA_MODE", "live")
    host: str = _env("RSS_HOST", "0.0.0.0")
    port: int = _env("RSS_PORT", 8000, int)

    sec_user_agent: str = _env(
        "SEC_USER_AGENT", "rs-reversal-terminal/1.0 (contact@example.com)"
    )
    http_timeout: int = _env("RSS_HTTP_TIMEOUT", 20, int)
    cache_ttl_minutes: int = _env("RSS_CACHE_TTL", 30, int)

    # سقف مرشحي الكون المجلوبين من Yahoo screener (تعدد صفحات حتى هذا العدد)
    universe_max_candidates: int = _env("RSS_UNIVERSE_MAX_CANDIDATES", 1000, int)

    # قائمة الأسهم المراقبة يدوياً (تُستخدم إذا تعذّر بناء الكون تلقائياً)
    watchlist_file: Path = CONFIG_DIR / "watchlist.csv"
    universe_file: Path = CONFIG_DIR / "universe.csv"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["watchlist_file"] = str(self.watchlist_file)
        d["universe_file"] = str(self.universe_file)
        return d


settings = Settings()


# --------------------------------------------------------------------------- #
#  معاملات الاستراتيجية  (Strategy rule-set)
# --------------------------------------------------------------------------- #
@dataclass
class StrategyParams:
    """شروط الاستراتيجية كما في ورقة الاستراتيجية.

    الفكرة: سهم أُجري له تقسيم عكسي (1:2 حتى 1:60) خلال آخر 60 يوماً،
    صعد صعوداً محدوداً يوم التقسيم ثم دخل موجة هبوط قوية (-30% أو أكثر)،
    وصل إلى تشبع بيعي (RSI < 30)، كوّن قاعاً وثبت عليه 3-5 جلسات،
    فيبدأ النظام برصده ومتابعته ثم ترقيته للمرحلة الإيجابية عند تحسّن
    المؤشرات واختراق القناة الهابطة.
    """

    # --- 1) شرط التقسيم العكسي ------------------------------------------- #
    rs_lookback_days: int = 60          # أقصى عمر للتقسيم العكسي (يوم تقويمي)
    rs_min_ratio: float = 2.0           # 1-for-2  (أصغر تقسيم عكسي نقبله)
    rs_max_ratio: float = 60.0          # 1-for-60 (أكبر تقسيم عكسي نقبله)

    # --- 2) صعود يوم التقسيم -------------------------------------------- #
    spike_window_sessions: int = 5      # نافذة البحث عن قمة ما بعد التقسيم
    min_spike_pct: float = 5.0          # أدنى صعود مقبول يوم التقسيم
    ideal_spike_pct: float = 15.0       # الصعود المثالي (نقطة كاملة)

    # --- 3) الموجة الهابطة ---------------------------------------------- #
    min_drop_pct: float = 30.0          # أدنى هبوط من قمة ما بعد التقسيم
    good_drop_pct: float = 40.0         # هبوط جيد
    strong_drop_pct: float = 50.0       # هبوط قوي جداً
    require_below_rs_open: bool = False  # هل نطلب كسر سعر افتتاح يوم التقسيم

    # --- 4) التشبع البيعي ------------------------------------------------ #
    rsi_period: int = 14
    rsi_oversold: float = 30.0          # النزول تحت 30
    rsi_deep_oversold: float = 25.0     # تشبع عميق
    rsi_extreme_oversold: float = 20.0  # تشبع متطرف (أفضل السيناريوهات)
    rsi_recovery_min: float = 35.0      # بداية التحسّن الفعلي
    rsi_recovery_delta: float = 5.0     # ارتفاع RSI عن قاعه بهذه القيمة

    # --- 5) القاع والثبات ------------------------------------------------ #
    min_base_sessions: int = 3          # أقل عدد جلسات ثبات (شرط الدخول للرصد)
    max_base_sessions: int = 20         # بعدها تعتبر القاعدة هالكة (تتحلل)
    sweet_base_max: int = 6             # النطاق المثالي 3-6 جلسات
    base_tolerance_pct: float = 9.0     # ثبات داخل ±9% من القاع المرجعي
    break_low_tolerance_pct: float = 3.0  # كسر القاع بأكثر من 3% = إلغاء السيناريو
    max_volume_dryup_ratio: float = 0.9  # جفاف الحجم: متوسط آخر 3 جلسات / 20 جلسة

    # --- 6) تأكيد الانعكاس ---------------------------------------------- #
    ema_fast: int = 9
    ema_mid: int = 20
    ema_slow: int = 50
    rvol_threshold: float = 1.5         # حجم نسبي لاعتماد الاختراق
    min_sessions_above_ema9: int = 2    # إغلاقان فوق EMA9 لتأكيد الاستحواذ
    require_macd_turn: bool = True      # تحسّن MACD شرط للترقية للمرحلة الإيجابية
    require_h4_alignment: bool = False  # توافق فريم 4 ساعات (يعطي نقاطاً إضافية)

    # --- 7) دورة الحياة بعد الثبات (شرط «جاهز فنياً») ------------------- #
    require_lifecycle_sequence: bool = True   # إلزام التسلسل الكامل للترقية للمرحلة 6
    ideal_impulse_pct: float = 20.0      # الانطلاقة المثالية بعد الثبات (≈20%)
    min_ready_impulse_pct: float = 15.0  # أدنى انطلاقة مقبولة (20% ± قليل)
    max_nearby_resistance_pct: float = 28.0  # «مقاومة قريبة» = لا تبعد أكثر من 28% عن الدعم الرئيسي
    resistance_stall_pct: float = 3.0    # يجب أن يتوقف السعر عند المقاومة داخل هذه النسبة
    max_lifecycle_sessions: int = 45     # أقصى عمر للتسلسل بعد الثبات قبل اعتباره قديماً
    support_retest_tolerance_pct: float = 2.0  # تسامح العودة لاختبار الدعم الرئيسي
    sweep_tolerance_pct: float = 1.0     # اختراق تحت الدعم بهذه النسبة أو أكثر = سحب سيولة
    reclaim_buffer_pct: float = 0.5      # الإغلاق فوق الدعم بهذه النسبة = استعادة
    min_reclaim_sessions: int = 1        # أدنى عدد جلسات استقرار فوق الدعم بعد السحب

    # --- 8) الموانع والمخاطر (Filters) ---------------------------------- #
    max_float_shares: float = 0.0       # 0 = بدون حد أقصى
    min_price: float = 0.5              # تجاهل ما دون هذا السعر
    max_price: float = 20.0             # الاستراتيجية تستهدف أسهم البيني
    min_avg_dollar_volume: float = 300_000  # سيولة دنيا
    hard_block_recent_offering_days: int = 10  # طرح أسهم خلال 10 أيام = خطر عالٍ

    # --- 9) الأوزان (مجموع 100) ----------------------------------------- #
    weights: dict[str, float] = field(
        default_factory=lambda: {
            "rs_setup": 18,        # حدث التقسيم العكسي وحداثته ونسبته
            "spike": 8,            # جودة صعود يوم التقسيم
            "decline": 14,         # عمق الموجة الهابطة وسطوعها
            "oversold": 14,        # تشبع RSI وتحسّنه
            "base": 18,            # جودة القاع والثبات وجفاف الحجم
            "reversal": 18,        # تأكيد الانعكاس (EMA/MACD/بنية/قناة/اكتمال التسلسل)
            "squeeze": 6,          # العوامل المساعدة للانفجار (فلوت/بيع مكشوف)
            "risk": 4,             # خصم مخاطر التخفيف والأخبار السلبية
        }
    )

    base_rate: float = 100.0            # مجموع النقاط النهائي (0..100)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


STRATEGY = StrategyParams()

# تعديلات اختيارية من متغيرات البيئة
STRATEGY.rs_lookback_days = _env("RSS_RS_WINDOW_DAYS", STRATEGY.rs_lookback_days, int)
STRATEGY.min_drop_pct = _env("RSS_MIN_DROP_PCT", STRATEGY.min_drop_pct, float)
STRATEGY.min_base_sessions = _env("RSS_MIN_BASE_SESSIONS", STRATEGY.min_base_sessions, int)
STRATEGY.max_base_sessions = _env("RSS_MAX_BASE_SESSIONS", STRATEGY.max_base_sessions, int)
STRATEGY.rsi_oversold = _env("RSS_RSI_OVERSOLD", STRATEGY.rsi_oversold, float)
STRATEGY.rvol_threshold = _env("RSS_MIN_RVOL", STRATEGY.rvol_threshold, float)
