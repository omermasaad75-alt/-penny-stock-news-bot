"""المخدم — FastAPI + لوحة داشبورد احترافية.

Endpoints:
  GET  /                       لوحة الداشبورد
  GET  /api/health             الحالة + المزوّدون + معاملات الاستراتيجية
  POST /api/scan/start         بدء مسح السوق في الخلفية
  GET  /api/scan/status        نسبة تقدّم المسح
  GET  /api/scan/results       نتائج المسح (جدول الرصد)
  GET  /api/analysis/{symbol}  تحليل كامل لرمز (يشمل السلاسل الزمنية)
  GET  /api/chart/{symbol}     حِزمة الرسم (1D / 4H)
  GET  /api/strategy           معاملات الاستراتيجية
  GET  /api/universe           قائمة الرموز المرصودة
  GET  /api/report/{symbol}    تقرير HTML/MD/JSON
  GET  /reports/{name}         ملفات التقارير المولّدة
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .config import REPORTS_DIR, STRATEGY, WEB_DIR, settings
from .providers.hub import DataHub, get_hub
from .reporting import analysis_to_markdown, markdown_to_html, watchlist_markdown, write_reports
from .strategy import engine as eng
from .strategy.charting import build_chart_payload

app = FastAPI(title=settings.app_name, version=settings.version)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
#  مدير المسح (خلفية + تتبّع تقدّم)
# --------------------------------------------------------------------------- #
class ScanManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.state: dict[str, Any] = {
            "running": False, "progress": 0, "total": 0, "current": "",
            "started_at": None, "finished_at": None, "error": None, "mode": None,
        }
        self.results: list[eng.Analysis] = []
        self.by_symbol: dict[str, eng.Analysis] = {}
        self.sources: dict[str, Any] = {}

    # ----------------------------- #
    def start(self, mode: str | None = None, symbols: list[str] | None = None,
              limit: int = 60) -> bool:
        with self.lock:
            if self.state["running"]:
                return False
            hub = get_hub(mode=mode, refresh=bool(mode))
            syms = symbols or hub.universe(limit=limit)
            self.state.update({
                "running": True, "progress": 0, "total": len(syms), "current": "",
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "finished_at": None, "error": None, "mode": hub.effective_mode,
            })
            self.thread = threading.Thread(target=self._run, args=(hub, syms), daemon=True)
            self.thread.start()
            return True

    def _run(self, hub: DataHub, syms: list[str]) -> None:
        try:
            out: list[eng.Analysis] = []
            for i, sym in enumerate(syms):
                try:
                    a = eng.analyze_symbol(sym, hub, STRATEGY)
                except Exception as exc:  # noqa: BLE001
                    a = eng.Analysis(symbol=sym)
                    a.rejections.append(f"خطأ في التحليل: {exc}")
                out.append(a)
                with self.lock:
                    self.state.update(progress=i + 1, current=sym)
            out.sort(key=lambda x: (-x.phase, -x.score))
            with self.lock:
                self.results = out
                self.by_symbol = {a.symbol: a for a in out}
                self.sources = hub.sources_report()
                self.state.update(running=False, finished_at=datetime.now().isoformat(timespec="seconds"),
                                  current="")
        except Exception as exc:  # noqa: BLE001
            with self.lock:
                self.state.update(running=False, error=str(exc)[:300])


SCAN = ScanManager()


def _summary(results: list[eng.Analysis]) -> dict[str, Any]:
    phase_counts: dict[str, int] = {}
    for a in results:
        key = str(a.phase if (a.eligible or not a.rs_found) else 0)
        phase_counts[key] = phase_counts.get(key, 0) + 1
    with_rs = [a for a in results if a.rs_found]
    elig = [a for a in results if a.eligible]
    watch = [a for a in results if a.watchlist]
    ready = [a for a in results if a.phase >= 5 and a.eligible]
    return {
        "scanned": len(results),
        "with_reverse_split": len(with_rs),
        "eligible": len(elig),
        "watchlist": len(watch),
        "ready": len(ready),
        "avg_score_watchlist": round(sum(a.score for a in watch) / len(watch), 1) if watch else 0,
        "phase_counts": phase_counts,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


# --------------------------------------------------------------------------- #
#  API
# --------------------------------------------------------------------------- #
@app.get("/api/health")
def health() -> dict[str, Any]:
    hub = get_hub()
    return {
        "app": settings.app_name, "app_ar": settings.app_name_ar, "version": settings.version,
        "server_time": datetime.now().isoformat(timespec="seconds"),
        "data": hub.sources_report(),
        "scan": {k: v for k, v in SCAN.state.items() if k != "error"},
        "results_cached": len(SCAN.results),
    }


@app.get("/api/strategy")
def strategy_params() -> dict[str, Any]:
    return {
        "params": STRATEGY.to_dict(),
        "phases": {k: v for k, v in eng.PHASES.items()},
        "scoring_keys": {
            "rs_setup": "حدث التقسيم العكسي", "spike": "صعود يوم التقسيم",
            "decline": "قوة الموجة الهابطة", "oversold": "التشبع البيعي",
            "base": "القاع والثبات", "reversal": "تأكيد الانعكاس",
            "squeeze": "عوامل الضغط والانفجار", "risk": "جودة المخاطر",
        },
    }


@app.get("/api/universe")
def universe(limit: int = Query(60, ge=1, le=500), mode: str | None = None) -> dict[str, Any]:
    hub = get_hub(mode=mode, refresh=bool(mode))
    return {"mode": hub.effective_mode, "symbols": hub.universe(limit)}


@app.post("/api/scan/start")
def scan_start(mode: str | None = None, limit: int = Query(60, ge=1, le=500),
               symbols: str | None = None) -> dict[str, Any]:
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else None
    started = SCAN.start(mode=mode, symbols=syms, limit=limit)
    return {"started": started, **SCAN.state}


@app.get("/api/scan/status")
def scan_status() -> dict[str, Any]:
    total = SCAN.state["total"] or 1
    pct = round(100 * SCAN.state["progress"] / total, 1)
    return {**SCAN.state, "percent": pct,
            "results_available": len(SCAN.results)}


@app.get("/api/scan/results")
def scan_results(only_watch: bool = False, include_rejected: bool = True) -> dict[str, Any]:
    rows = SCAN.results
    if only_watch:
        rows = [a for a in rows if a.watchlist]
    elif not include_rejected:
        rows = [a for a in rows if a.eligible]
    return {
        "summary": _summary(SCAN.results),
        "sources": SCAN.sources,
        "rows": [a.to_dict() for a in rows],
    }


@app.get("/api/analysis/{symbol}")
def analysis(symbol: str, mode: str | None = None, with_series: bool = True) -> dict[str, Any]:
    symbol = symbol.upper()
    a = SCAN.by_symbol.get(symbol)
    if a is None:
        hub = get_hub(mode=mode, refresh=bool(mode))
        try:
            a = eng.analyze_symbol(symbol, hub, STRATEGY)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    return a.to_dict(with_series=with_series)


@app.get("/api/chart/{symbol}")
def chart(symbol: str, tf: str = "1D", mode: str | None = None) -> dict[str, Any]:
    hub = get_hub(mode=mode, refresh=bool(mode))
    payload = build_chart_payload(symbol.upper(), tf, hub, STRATEGY)
    a = SCAN.by_symbol.get(symbol.upper())
    payload["levels"] = (a.levels if a else {}) or {}
    payload["phase"] = a.phase if a else 0
    return payload


@app.get("/api/report/{symbol}")
def report(symbol: str, fmt: str = "html", mode: str | None = None) -> Any:
    symbol = symbol.upper()
    a = SCAN.by_symbol.get(symbol)
    if a is None:
        hub = get_hub(mode=mode, refresh=bool(mode))
        a = eng.analyze_symbol(symbol, hub, STRATEGY)
    md = analysis_to_markdown(a, SCAN.sources or None)
    if fmt == "md":
        return PlainTextResponse(md, media_type="text/markdown; charset=utf-8")
    if fmt == "json":
        return JSONResponse(a.to_dict())
    return HTMLResponse(markdown_to_html(md, f"{symbol} — تقرير تحليلي"))


@app.post("/api/report/save")
def report_save(symbols: str | None = None) -> dict[str, Any]:
    syms = ([s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols
            else [a.symbol for a in SCAN.results if a.rs_found])
    if not syms:
        raise HTTPException(status_code=400, detail="لا توجد رموز — شغّل المسح أولاً")
    hub = get_hub()
    written = write_reports(syms, hub, REPORTS_DIR, analyses=SCAN.by_symbol)
    # تقرير القائمة الموحّد
    if SCAN.results:
        wl = watchlist_markdown(SCAN.results, SCAN.sources)
        stamp = datetime.now().strftime("%Y%m%d")
        (REPORTS_DIR / f"watchlist_{stamp}.md").write_text(wl, encoding="utf-8")
        (REPORTS_DIR / f"watchlist_{stamp}.html").write_text(
            markdown_to_html(wl, "قائمة الفرص"), encoding="utf-8")
    return {"written": written, "folder": str(REPORTS_DIR)}


@app.get("/api/demo-metadata")
def demo_metadata() -> dict[str, Any]:
    from .providers.demo import demo_metadata as dm
    return {"scenarios": dm()}


# --------------------------------------------------------------------------- #
#  الواجهة
# --------------------------------------------------------------------------- #
app.mount("/assets", StaticFiles(directory=str(WEB_DIR / "assets")), name="assets")
app.mount("/vendor", StaticFiles(directory=str(WEB_DIR / "vendor")), name="vendor")


@app.get("/", response_class=HTMLResponse)
def index() -> Any:
    idx = WEB_DIR / "index.html"
    if not idx.exists():
        return HTMLResponse("<h1>لوحة الداشبورد غير مثبّتة</h1>")
    return FileResponse(idx)


@app.get("/reports/{name}")
def report_file(name: str) -> Any:
    path = (REPORTS_DIR / name).resolve()
    if not str(path).startswith(str(REPORTS_DIR.resolve())) or not path.exists():
        raise HTTPException(status_code=404, detail="الملف غير موجود")
    media = "text/html" if path.suffix == ".html" else "text/plain"
    return FileResponse(path, media_type=f"{media}; charset=utf-8")


@app.get("/favicon.ico")
def favicon():
    return JSONResponse({}, status_code=204)


def main() -> None:
    """تشغيل المخدم: python -m app.server"""
    print(f"▶ {settings.app_name_ar} v{settings.version}")
    print(f"▶ وضع البيانات: {settings.data_mode} | المنفذ: {settings.port}")
    print(f"▶ افتح المتصفح على: http://localhost:{settings.port}")
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
