.PHONY: install run scan report test clean demo

PY ?= python3

install:            ## تثبيت المتطلبات
	$(PY) -m pip install -r requirements.txt

run:                ## تشغيل لوحة الداشبورد  (http://localhost:8000)
	$(PY) -m app.server

scan:               ## مسح السوق من الطرفية وطباعة جدول الرصد
	$(PY) -m app.scanner --scan

report:             ## توليد تقرير تحليلي Markdown لكل سهم مؤهل
	$(PY) -m app.report --all --out reports/output

demo:               ## توليد بيانات تجريبية محلياً (بدون انترنت)
	$(PY) -m app.scanner --scan --mode demo

test:               ## تشغيل الاختبارات
	$(PY) -m pytest -q

clean:
	rm -rf data/cache/*.json reports/output/*.md
	find . -name __pycache__ -type d -exec rm -rf {} +
