"""حاجز نشر مستقل؛ لا يكفي وسم الوضع العام لإثبات حقيقة الصفوف."""
from __future__ import annotations
import json
import sys
from pathlib import Path

SOURCE_KEYS = ("price_source", "fundamentals_source", "news_source")
BLOCKED = {"NVEX", "GNSX", "BIOK", "RUBY", "SRNA"}


def validate(payload: dict) -> list[str]:
    errors = []
    if payload.get("mode_effective", payload.get("mode")) != "live":
        errors.append("الوضع الفعّال ليس live")
    for row in payload.get("rows", []):
        sym = row.get("symbol", "؟")
        if sym in BLOCKED:
            errors.append(f"{sym}: رمز محظور")
        if row.get("synthetic") is not False:
            errors.append(f"{sym}: بيانات تجريبية أو مجهولة")
        if not all(k in row for k in SOURCE_KEYS):
            errors.append(f"{sym}: حقول مصادر مفقودة")
        if not row.get("price_source") or not row.get("fundamentals_source") or not row.get("last_close", 0) > 0:
            errors.append(f"{sym}: بلا مصدر أسعار/أساسيات حقيقي")
        # المصادر الاختيارية يمكن أن تكون فارغة؛ لا ننسب بيانات لمصدر لم يُعدها.
        for key in (*SOURCE_KEYS, "short_source"):
            value = str(row.get(key, "")).lower()
            if "demo" in value or "synthetic" in value or value == "unknown":
                errors.append(f"{sym}: مصدر غير حقيقي في {key}")
        for key in ("catalysts", "dilution"):
            for item in row.get(key, []):
                source = str(item.get("source", "")).lower()
                if not source or "demo" in source or source == "unknown":
                    errors.append(f"{sym}: حدث بلا مصدر حقيقي")
    return errors


def main() -> int:
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    errors = validate(payload)
    if errors:
        print("✖ رفض النشر:\n" + "\n".join(errors))
        return 1
    print(f"✔ نشر صادق: live، synthetic=0، {len(payload.get('rows', []))} صف؛ المصادر الناقصة صريحة")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
