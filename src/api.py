"""FastAPI wrapper for the web-health-bot.

Endpoints:
  GET  /                     — single-page UI
  GET  /health               — liveness + backend info
  GET  /targets              — default URLs from targets.yaml
  POST /check                — run the bot on one URL, return its PageReport
  GET  /artifacts/{name}     — screenshot file (read-only)

Design note: `/check` runs Playwright synchronously inside FastAPI's
threadpool (the route is `def`, not `async def`). That's fine for a
demo / UI usage — one user clicking buttons. For real concurrent traffic
you'd want either an async Playwright variant or a job-queue worker.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from playwright.sync_api import sync_playwright

from . import bot
from .config import settings, vision_backend
from .schemas import PageReport

ROOT = Path(__file__).resolve().parent.parent
UI_DIR = ROOT / "ui"
TARGETS_FILE = ROOT / "targets.yaml"

logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(
    title="Web Health Bot",
    description="Walks pages and decides if they're broken. Deterministic checks first, vision-LLM only on ambiguity.",
    version="0.1.0",
)


# --- request / response models -------------------------------------------

class CheckRequest(BaseModel):
    url: str = Field(min_length=4, max_length=2048, description="URL to probe.")


class HealthResponse(BaseModel):
    status: str
    vision_backend: str
    headless: bool
    sim_short_circuit_rules: list[str]


class TargetsResponse(BaseModel):
    items: list[dict]


# --- routes --------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        vision_backend=vision_backend(),
        headless=settings.headless,
        sim_short_circuit_rules=[
            "hard deterministic FAIL → skip LLM",
            "all deterministic green → skip LLM",
            "ambiguous → vision LLM",
        ],
    )


@app.get("/targets", response_model=TargetsResponse)
def targets() -> TargetsResponse:
    if not TARGETS_FILE.exists():
        return TargetsResponse(items=[])
    with open(TARGETS_FILE, encoding="utf-8") as f:
        data = yaml.safe_load(f) or []
    items: list[dict] = []
    for it in data:
        if isinstance(it, str):
            items.append({"url": it, "note": ""})
        elif isinstance(it, dict) and "url" in it:
            items.append({"url": it["url"], "note": it.get("note", "")})
    return TargetsResponse(items=items)


@app.post("/check", response_model=PageReport)
def check(req: CheckRequest) -> PageReport:
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=settings.headless)
            try:
                return bot.check_page(browser, req.url)
            finally:
                browser.close()
    except Exception as e:  # noqa: BLE001
        log.exception("check failed for %s", req.url)
        raise HTTPException(status_code=500, detail=f"check failed: {e!s}") from e


# --- screenshot artifacts -----------------------------------------------

@app.get("/artifacts/{filename}", include_in_schema=False)
def artifact(filename: str) -> FileResponse:
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "invalid filename")
    path = settings.artifacts_dir / filename
    if not path.exists():
        raise HTTPException(404, "not found")
    return FileResponse(path)


# --- UI mount ------------------------------------------------------------

if UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")

    @app.get("/", include_in_schema=False)
    def root() -> FileResponse:
        return FileResponse(UI_DIR / "index.html")
