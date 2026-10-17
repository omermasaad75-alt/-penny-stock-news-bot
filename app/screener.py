"""طبقة الفحص والترشيح المرنة — المصدر الإلزامي خمسة فلاتر فقط.

هذه الوحدة لا تعيد تنفيذ التحليل الفني؛ بل تبني معاملات الفحص من ملف إعداد/CLI،
تتحقق من صحتها، ترتّب المؤهلين تنازلياً بالنقاط الفنية، وتصدّر CSV قابل للتدقيق.
الأهلية نفسها تُحسب داخل ``app.strategy.engine`` من ``core_filters`` الخمسة فقط.
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import fields as dataclass_fields, replace
from pathlib import Path
from typing import Any, Iterable, Mapping

from .config import STRATEGY, StrategyParams
from .strategy.engine import CORE_FILTER_KEYS, Analysis, core_filters_passed

WEIGHT_KEYS: tuple[str, ...] = ("volume", "patterns", "support", "ema")

# أسماء مختصرة مقبولة في JSON/CLI، مع الإبقاء على أسماء StrategyParams كما هي.
PARAM_ALIASES: dict[str, str] = {
    "price_min": "min_price",
    "price_max": "max_price",
    "rs_window_days": "rs_lookback_days",
    "rs_window": "rs_lookback_days",
    "min_drop": "min_drop_pct",
    "drawdown_pct": "min_drop_pct",
    "rsi_threshold": "rsi_oversold",
    "rsi_max": "rsi_oversold",
    "max_short": "max_short_shares",
    "short_shares_max": "max_short_shares",
    "score_weights": "weights",
}

CSV_COLUMNS: list[tuple[str, str]] = [
    ("rank", "الترتيب"),
    ("symbol", "الرمز"),
    ("name", "الاسم"),
    ("score", "النقاط الفنية"),
    ("grade", "الدرجة"),
    ("last_close", "آخر إغلاق $"),
    ("rs_date", "تاريخ التقسيم"),
    ("rs_age_days", "عمر التقسيم (يوم)"),
    ("rs_ratio", "نسبة التقسيم"),
    ("post_split_peak", "قمة ما بعد التقسيم $"),
    ("decline_pct", "الهبوط من القمة %"),
    ("rsi_trough", "أدنى RSI"),
    ("rsi_trough_date", "تاريخ أدنى RSI"),
    ("short_shares", "الأسهم المكشوفة"),
    ("volume_score", "درجة جفاف الحجم"),
    ("patterns_score", "درجة النماذج"),
    ("support_score", "درجة الدعم/السحب"),
    ("ema_score", "درجة المتوسطات"),
    ("primary_pattern", "أبرز نموذج"),
    ("core_price", "فلتر السعر 1–5$"),
    ("core_reverse_split", "فلتر التقسيم العكسي ≤60 يوم"),
    ("core_decline", "فلتر الهبوط ≥30%"),
    ("core_rsi", "فلتر RSI<30"),
    ("core_short_shares", "فلتر الأسهم المكشوفة <20,000"),
    ("eligible", "مؤهل"),
    ("rejections", "أسباب الاستبعاد"),
]


class ScreenConfigError(ValueError):
    """إعداد فحص غير صالح — يُعرض للمستخدم بدل تجاهله بصمت."""


def parse_weights(text: str) -> dict[str, float]:
    """يقرأ أوزاناً بصيغة ``volume=25,patterns=35,support=25,ema=15``."""
    if not text or not str(text).strip():
        return {}
    out: dict[str, float] = {}
    for chunk in str(text).replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise ScreenConfigError(f"وزن غير صالح «{chunk}» — استخدم key=value")
        key, _, raw = chunk.partition("=")
        key = key.strip().lower()
        if key not in WEIGHT_KEYS:
            raise ScreenConfigError(f"مفتاح وزن غير معروف «{key}»")
        try:
            value = float(raw)
        except ValueError as exc:
            raise ScreenConfigError(f"قيمة وزن غير رقمية «{raw}»") from exc
        if not math.isfinite(value) or value < 0:
            raise ScreenConfigError(f"الوزن «{key}» يجب أن يكون رقماً غير سالب")
        out[key] = value
    return out


def _flatten_config(data: Mapping[str, Any]) -> dict[str, Any]:
    """يقبل إعداداً مسطحاً أو مجمّعاً تحت core/scoring/strategy/params."""
    if not isinstance(data, Mapping):
        raise ScreenConfigError("ملف الإعداد يجب أن يكون كائن JSON")
    out: dict[str, Any] = {}
    for key, value in data.items():
        if str(key).startswith("_") or key in {"description", "notes", "version"}:
            continue
        if key in {"strategy", "params", "core", "score", "scoring"}:
            if not isinstance(value, Mapping):
                raise ScreenConfigError(f"القسم «{key}» يجب أن يكون كائناً")
            out.update(_flatten_config(value))
        else:
            out[str(key)] = value
    return out


def load_overrides(path: str | Path) -> dict[str, Any]:
    """تحميل إعداد JSON مع قبول الأسماء المختصرة."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ScreenConfigError(f"ملف الإعداد غير موجود: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ScreenConfigError(f"JSON غير صالح في {path}: {exc}") from exc
    return _flatten_config(raw)


def _coerce_value(key: str, value: Any, default: Any) -> Any:
    if key == "weights":
        if not isinstance(value, Mapping):
            raise ScreenConfigError("weights يجب أن يكون كائناً {volume, patterns, support, ema}")
        out: dict[str, float] = {}
        for wkey, wval in value.items():
            wkey = str(wkey).lower()
            if wkey not in WEIGHT_KEYS:
                raise ScreenConfigError(f"مفتاح وزن غير معروف «{wkey}»")
            try:
                num = float(wval)
            except (TypeError, ValueError) as exc:
                raise ScreenConfigError(f"وزن «{wkey}» غير رقمي") from exc
            if not math.isfinite(num) or num < 0:
                raise ScreenConfigError(f"وزن «{wkey}» يجب أن يكون غير سالب")
            out[wkey] = num
        return out
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(float(value))
        except (TypeError, ValueError) as exc:
            raise ScreenConfigError(f"«{key}» يجب أن يكون عدداً صحيحاً") from exc
    if isinstance(default, float):
        try:
            num = float(value)
        except (TypeError, ValueError) as exc:
            raise ScreenConfigError(f"«{key}» يجب أن يكون رقماً") from exc
        if not math.isfinite(num):
            raise ScreenConfigError(f"«{key}» يجب أن يكون رقماً منتهياً")
        return num
    return value


def validate_params(params: StrategyParams) -> StrategyParams:
    """يتحقق من الفلاتر الخمسة والأوزان قبل أي مسح، ويمنع قيماً متناقضة."""
    if params.min_price < 0 or params.max_price <= params.min_price:
        raise ScreenConfigError("يجب أن يكون 0 ≤ min_price < max_price")
    if params.rs_lookback_days < 0:
        raise ScreenConfigError("rs_lookback_days لا يمكن أن يكون سالباً")
    if not 0 <= params.min_drop_pct <= 100:
        raise ScreenConfigError("min_drop_pct يجب أن يكون بين 0 و100")
    if params.rsi_period < 2 or not 0 < params.rsi_oversold < 100:
        raise ScreenConfigError("rsi_period ≥ 2 و 0 < rsi_oversold < 100")
    if params.max_short_shares <= 0:
        raise ScreenConfigError("max_short_shares يجب أن يكون أكبر من صفر")
    if params.rs_min_ratio < 0:
        raise ScreenConfigError("rs_min_ratio لا يمكن أن يكون سالباً")
    if params.rs_max_ratio and params.rs_max_ratio <= params.rs_min_ratio:
        raise ScreenConfigError("rs_max_ratio يجب أن يكون أكبر من rs_min_ratio أو صفراً")
    weights = params.weights or {}
    missing = [k for k in WEIGHT_KEYS if k not in weights]
    if missing:
        raise ScreenConfigError("أوزان ناقصة: " + ", ".join(missing))
    if any(not math.isfinite(float(weights[k])) or float(weights[k]) < 0 for k in WEIGHT_KEYS):
        raise ScreenConfigError("كل الأوزان يجب أن تكون أرقاماً غير سالبة")
    if sum(float(weights[k]) for k in WEIGHT_KEYS) <= 0:
        raise ScreenConfigError("مجموع الأوزان يجب أن يكون أكبر من صفر")
    return params


def build_params(overrides: Mapping[str, Any] | None = None,
                 base: StrategyParams | None = None) -> StrategyParams:
    """ينشئ نسخة مستقلة من StrategyParams مع تطبيق تعديلات مفهومة فقط."""
    params = replace(base or STRATEGY)
    params.weights = dict(getattr(params, "weights", {}) or {})
    allowed = {f.name for f in dataclass_fields(StrategyParams)}
    for raw_key, value in (overrides or {}).items():
        key = PARAM_ALIASES.get(str(raw_key).strip().lower(), str(raw_key).strip())
        if key not in allowed:
            raise ScreenConfigError(f"معامل غير معروف «{raw_key}»")
        default = getattr(params, key)
        coerced = _coerce_value(key, value, default)
        if key == "weights":
            merged = dict(getattr(params, "weights", {}) or {})
            merged.update(coerced)
            coerced = merged
        setattr(params, key, coerced)
    return validate_params(params)


def rank_eligible(results: Iterable[Analysis]) -> list[Analysis]:
    """الجدول الرئيسي: المؤهلون فقط، تنازلياً بالنقاط ثم الرمز أبجدياً."""
    eligible = [a for a in results if a.eligible and core_filters_passed(a)]
    return sorted(eligible, key=lambda a: (-float(a.score or 0), a.symbol))


def rank_all(results: Iterable[Analysis]) -> list[Analysis]:
    return sorted(results, key=lambda a: (-float(a.score or 0), a.symbol))


def core_status(a: Analysis) -> dict[str, bool]:
    return {str(f.get("key")): bool(f.get("ok")) for f in (a.core_filters or [])}


def _score_part(a: Analysis, key: str) -> float | None:
    for part in a.breakdown or []:
        if part.get("key") == key:
            return float(part.get("score") or 0)
    return None


def _primary_pattern(a: Analysis) -> str:
    candidates = [
        p for p in (a.patterns_daily or [])
        if p.get("bias") == "bullish" and p.get("status") != "invalidated"
        and p.get("name") in {"double_bottom", "falling_wedge", "rsi_divergence"}
    ]
    if not candidates:
        return ""
    best = max(candidates, key=lambda p: float(p.get("confidence") or 0))
    return str(best.get("name_ar") or best.get("name") or "")


def row_for_csv(a: Analysis, rank: int) -> dict[str, Any]:
    status = core_status(a)
    return {
        "rank": rank,
        "symbol": a.symbol,
        "name": a.name,
        "score": a.score,
        "grade": a.grade,
        "last_close": a.last_close,
        "rs_date": a.rs_date,
        "rs_age_days": a.rs_age_days,
        "rs_ratio": a.rs_ratio_str,
        "post_split_peak": a.spike_high,
        "decline_pct": a.decline_pct,
        "rsi_trough": a.rsi_trough,
        "rsi_trough_date": a.rsi_trough_date,
        "short_shares": a.short_shares,
        "volume_score": _score_part(a, "volume"),
        "patterns_score": _score_part(a, "patterns"),
        "support_score": _score_part(a, "support"),
        "ema_score": _score_part(a, "ema"),
        "primary_pattern": _primary_pattern(a),
        "core_price": "نعم" if status.get("price") else "لا",
        "core_reverse_split": "نعم" if status.get("reverse_split") else "لا",
        "core_decline": "نعم" if status.get("decline") else "لا",
        "core_rsi": "نعم" if status.get("rsi") else "لا",
        "core_short_shares": "نعم" if status.get("short_shares") else "لا",
        "eligible": "نعم" if a.eligible else "لا",
        "rejections": " | ".join(a.rejections),
    }


def write_scan_csv(path: str | Path, results: Iterable[Analysis],
                   include_rejected: bool = False) -> int:
    """يكتب CSV مرتّباً؛ افتراضياً المؤهلون فقط، وبترميز UTF-8 BOM لإكسل."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = rank_all(results) if include_rejected else rank_eligible(results)
    with target.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=[key for key, _ in CSV_COLUMNS],
                                extrasaction="ignore")
        writer.writerow({key: label for key, label in CSV_COLUMNS})
        for i, analysis in enumerate(rows, 1):
            writer.writerow(row_for_csv(analysis, i))
    return len(rows)


def rules_snapshot(params: StrategyParams) -> dict[str, Any]:
    """وصف قابل للتسلسل للفلاتر الخمسة الفعّالة، يُضمَّن في JSON وAPI."""
    return {
        "eligibility": "five_hard_filters_only",
        "core_filters": [
            {"key": "price", "rule": f"{params.min_price:g} <= close <= {params.max_price:g}"},
            {"key": "reverse_split", "rule": f"executed within <= {params.rs_lookback_days} days; factor > {params.rs_min_ratio:g}"},
            {"key": "decline", "rule": f"drop >= {params.min_drop_pct:g}% from post-split peak"},
            {"key": "rsi", "rule": f"RSI({params.rsi_period}) < {params.rsi_oversold:g} during bottom formation"},
            {"key": "short_shares", "rule": f"short_shares < {params.max_short_shares:,.0f} (exclusive)"},
        ],
        "optional_score_weights": {k: float(params.weights.get(k, 0)) for k in WEIGHT_KEYS},
        "optional_signals_never_reject": [
            "volume_dryup", "double_bottom", "falling_wedge", "bullish_rsi_divergence",
            "support_test", "liquidity_sweep", "ema20_ema30_ema50",
        ],
    }


def core_keys() -> tuple[str, ...]:
    return CORE_FILTER_KEYS
