"""
OpenEye local server.

Serves the executive dashboard (static/) and a small JSON API over the SQLite store.
Read-only except for /api/scan, which kicks off pipeline.py in the background.

Run:  uv run uvicorn app:app --port 8500   (or: uv run python app.py)
Then open http://127.0.0.1:8500
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import db

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"

app = FastAPI(title="OpenEye Dashboard", version="0.1.0")

_scan_proc: "subprocess.Popen | None" = None  # the running pipeline.py process, if any


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


@app.get("/api/scans")
def api_scans() -> JSONResponse:
    """List all scans (newest first) for the historical lookup selector."""
    return JSONResponse(db.get_scans())


@app.get("/api/summary")
def api_summary(scan_id: int | None = None) -> JSONResponse:
    return JSONResponse(db.get_summary(scan_id))


@app.get("/api/listings")
def api_listings(verdict: str | None = None, scan_id: int | None = None) -> JSONResponse:
    return JSONResponse(db.get_listings(verdict, scan_id))


@app.get("/api/products")
def api_products(scan_id: int | None = None) -> JSONResponse:
    return JSONResponse(db.get_products(scan_id))


@app.get("/api/history")
def api_history(key: str) -> JSONResponse:
    return JSONResponse(db.get_history(key))


@app.get("/api/categories")
def api_categories(scan_id: int | None = None) -> JSONResponse:
    return JSONResponse(db.get_categories(scan_id))


@app.get("/api/profit_history")
def api_profit_history(category: str | None = None) -> JSONResponse:
    return JSONResponse(db.get_profit_history(category))


@app.get("/api/profit_points")
def api_profit_points(category: str | None = None) -> JSONResponse:
    return JSONResponse(db.get_profit_points(category))


@app.get("/api/scan/status")
def api_scan_status() -> JSONResponse:
    # Reflect the ACTUAL process state via poll() — self-correcting, never gets stuck.
    running = _scan_proc is not None and _scan_proc.poll() is None
    last_rc = None if (_scan_proc is None or running) else _scan_proc.returncode
    return JSONResponse({"running": running, "last_rc": last_rc})


@app.post("/api/scan")
def api_scan() -> JSONResponse:
    """Trigger a fresh scan (pipeline.py) in the background."""
    global _scan_proc
    if _scan_proc is not None and _scan_proc.poll() is None:
        return JSONResponse({"started": False, "reason": "already running"}, status_code=409)
    try:
        # Detached; the dashboard polls /api/scan/status and refreshes when it exits.
        _scan_proc = subprocess.Popen([sys.executable, str(HERE / "pipeline.py")], cwd=HERE)
    except Exception as e:
        return JSONResponse({"started": False, "reason": str(e)}, status_code=500)
    return JSONResponse({"started": True, "pid": _scan_proc.pid})


CLEAR_PHRASE = "DELETE ALL HISTORY"


@app.post("/api/clear")
def api_clear(confirm: str = "") -> JSONResponse:
    """Wipe all scan history. Requires the exact confirmation phrase server-side, in
    addition to the UI's confirm dialog + typed-phrase check. Irreversible."""
    if confirm != CLEAR_PHRASE:
        return JSONResponse(
            {"cleared": False, "reason": f"exact phrase '{CLEAR_PHRASE}' required"},
            status_code=400,
        )
    counts = db.clear_all()
    return JSONResponse({"cleared": True, "deleted": counts})


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8500, reload=False)
