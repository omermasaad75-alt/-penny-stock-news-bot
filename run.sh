#!/usr/bin/env bash
# ============================================================
#  تشغيل سريع: ينشئ بيئة افتراضية، يثبّت المتطلبات، ثم يشغّل الداشبورد
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

PY=${PY:-python3}

if [ ! -d ".venv" ]; then
  echo "▶ إنشاء بيئة افتراضية .venv ..."
  $PY -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "▶ تثبيت المتطلبات ..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "▶ تم إنشاء ملف .env (وضع البيانات: demo)"
fi

PORT=$(grep -E '^RSS_PORT=' .env | cut -d= -f2 || echo 8000)
echo "▶ لوحة الداشبورد تعمل على المنفذ ${PORT:-8000} ..."
exec python -m app.server
