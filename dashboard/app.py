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

_scan_state = {"running": False, "last_rc": None}


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


@app.get("/api/summary")
def api_summary() -> JSONResponse:
    return JSONResponse(db.get_summary())


@app.get("/api/listings")
def api_listings(verdict: str | None = None) -> JSONResponse:
    return JSONResponse(db.get_listings(verdict))


@app.get("/api/products")
def api_products() -> JSONResponse:
    return JSONResponse(db.get_products())


@app.get("/api/history")
def api_history(key: str) -> JSONResponse:
    return JSONResponse(db.get_history(key))


@app.get("/api/scan/status")
def api_scan_status() -> JSONResponse:
    return JSONResponse(_scan_state)


@app.post("/api/scan")
def api_scan() -> JSONResponse:
    """Trigger a fresh scan (pipeline.py) in the background."""
    if _scan_state["running"]:
        return JSONResponse({"started": False, "reason": "already running"}, status_code=409)

    def _done(rc: int) -> None:
        _scan_state["running"] = False
        _scan_state["last_rc"] = rc

    _scan_state["running"] = True
    try:
        # Detached; the dashboard polls /api/scan/status and refreshes when it clears.
        proc = subprocess.Popen([sys.executable, str(HERE / "pipeline.py")], cwd=HERE)
        _scan_state["pid"] = proc.pid
    except Exception as e:
        _scan_state["running"] = False
        return JSONResponse({"started": False, "reason": str(e)}, status_code=500)
    return JSONResponse({"started": True})


@app.post("/api/clear")
def api_clear(confirm: str = "") -> JSONResponse:
    """Wipe all scan history. Requires ?confirm=ERASE as a server-side second check
    (the UI also double-confirms before calling this)."""
    if confirm != "ERASE":
        return JSONResponse({"cleared": False, "reason": "confirmation token required"}, status_code=400)
    counts = db.clear_all()
    return JSONResponse({"cleared": True, "deleted": counts})


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8500, reload=False)
