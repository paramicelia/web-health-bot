"""Deterministic probe — Playwright-powered.

This layer produces black-and-white signals where the answer is
objective: HTTP status, broken resource count, console errors, page
title presence, main text length, load time. These never need an LLM.

Design principle: each signal is converted to at most one `Finding` at
a single severity. The orchestrator in `bot.py` combines findings into
an overall verdict.

Output is always a ProbeResult — even on hard failure (timeout, DNS
error). In that case the `error` field is set and subsequent layers
know to short-circuit.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import (
    Browser,
    ConsoleMessage,
    Page,
    Response,
    TimeoutError as PlaywrightTimeout,
)

from .config import settings
from .schemas import Finding

log = logging.getLogger(__name__)


@dataclass
class ProbeResult:
    url: str
    findings: list[Finding] = field(default_factory=list)
    http_status: int | None = None
    title: str = ""
    text_length: int = 0
    latency_ms: int = 0
    screenshot_path: str | None = None
    error: str | None = None


def _url_safe_filename(url: str) -> str:
    return (
        url.replace("https://", "")
        .replace("http://", "")
        .replace("/", "_")
        .replace(":", "_")
        .replace("?", "_")[:80]
    )


def probe(browser: Browser, url: str) -> ProbeResult:
    result = ProbeResult(url=url)
    page: Page = browser.new_page(viewport={"width": 1280, "height": 900})

    # --- collectors --------------------------------------------------------
    resource_failures: list[tuple[str, int]] = []   # (url, status)
    console_errors: list[str] = []
    main_response: Response | None = None

    def on_response(resp: Response) -> None:
        nonlocal main_response
        if main_response is None and resp.url == url:
            main_response = resp
        # Track any sub-resource that failed the request/response cycle.
        try:
            status = resp.status
        except Exception:  # noqa: BLE001 — playwright can raise on closed page
            return
        if status >= 400 and resp.url != url:
            resource_failures.append((resp.url, status))

    def on_console(msg: ConsoleMessage) -> None:
        if msg.type in ("error",):
            console_errors.append(msg.text[:200])

    page.on("response", on_response)
    page.on("console", on_console)

    # --- navigate ----------------------------------------------------------
    t0 = time.perf_counter()
    try:
        response = page.goto(url, timeout=settings.navigation_timeout_ms, wait_until="load")
        result.latency_ms = int((time.perf_counter() - t0) * 1000)
        if response is not None and main_response is None:
            main_response = response
    except PlaywrightTimeout as e:
        result.latency_ms = int((time.perf_counter() - t0) * 1000)
        result.error = f"navigation timeout after {result.latency_ms} ms"
        result.findings.append(Finding(
            layer="navigation", severity="fail",
            message=f"Timed out loading page after {result.latency_ms} ms",
            detail={"exception": str(e)[:200]},
        ))
        page.close()
        return result
    except Exception as e:  # noqa: BLE001
        result.latency_ms = int((time.perf_counter() - t0) * 1000)
        result.error = f"navigation error: {e!s}"
        result.findings.append(Finding(
            layer="navigation", severity="fail",
            message=f"Failed to load page: {str(e)[:140]}",
            detail={"exception": str(e)[:200]},
        ))
        page.close()
        return result

    # --- HTTP status -------------------------------------------------------
    status = main_response.status if main_response is not None else None
    result.http_status = status
    if status is None:
        result.findings.append(Finding(
            layer="http", severity="warn",
            message="No main response captured (possible redirect chain or prerender)",
        ))
    elif 200 <= status < 300:
        result.findings.append(Finding(
            layer="http", severity="ok",
            message=f"HTTP {status}",
            detail={"status": status},
        ))
    elif 300 <= status < 400:
        # After 'load' event we'd expect redirects resolved. A lingering 3xx
        # is unusual but not a hard fail.
        result.findings.append(Finding(
            layer="http", severity="warn",
            message=f"HTTP {status} — redirect not resolved",
            detail={"status": status},
        ))
    elif 400 <= status < 500:
        result.findings.append(Finding(
            layer="http", severity="fail",
            message=f"HTTP {status} — client error",
            detail={"status": status},
        ))
    else:  # 5xx
        result.findings.append(Finding(
            layer="http", severity="fail",
            message=f"HTTP {status} — server error",
            detail={"status": status},
        ))

    # --- broken sub-resources ----------------------------------------------
    n_broken = len(resource_failures)
    if n_broken > settings.max_broken_resources_fail:
        severity = "fail"
    elif n_broken > settings.max_broken_resources_warn:
        severity = "warn"
    else:
        severity = "ok"
    if n_broken:
        result.findings.append(Finding(
            layer="resources", severity=severity,
            message=f"{n_broken} sub-resource(s) failed to load (>=400)",
            detail={"samples": [{"url": u[:120], "status": s} for u, s in resource_failures[:6]]},
        ))

    # --- console errors ----------------------------------------------------
    if console_errors:
        # Console errors alone are rarely a hard fail — lots of healthy
        # prod sites throw them. Flag as warn at worst.
        severity = "warn" if len(console_errors) > 3 else "ok"
        if severity != "ok":
            result.findings.append(Finding(
                layer="console", severity=severity,
                message=f"{len(console_errors)} console error(s)",
                detail={"samples": console_errors[:5]},
            ))

    # --- page content ------------------------------------------------------
    try:
        result.title = (page.title() or "").strip()
    except Exception:  # noqa: BLE001
        result.title = ""
    try:
        body_text = page.evaluate("() => document.body && document.body.innerText || ''")
    except Exception:  # noqa: BLE001
        body_text = ""
    result.text_length = len(body_text or "")

    if not result.title:
        result.findings.append(Finding(
            layer="dom", severity="warn",
            message="Page has no <title> — unusual for a real page",
        ))

    if result.text_length < settings.min_text_length:
        # Below threshold could mean a blank / SPA-shell / placeholder.
        # This is ambiguous: SPA hydration, cold start, legit dashboard behind
        # login. Flag as warn and let the vision layer judge.
        result.findings.append(Finding(
            layer="dom", severity="warn",
            message=f"Rendered body text is very short ({result.text_length} chars)",
            detail={"threshold": settings.min_text_length, "title": result.title[:80]},
        ))

    # --- performance -------------------------------------------------------
    if result.latency_ms > settings.very_slow_threshold_ms:
        result.findings.append(Finding(
            layer="performance", severity="warn",
            message=f"Very slow load: {result.latency_ms} ms",
        ))
    elif result.latency_ms > settings.slow_threshold_ms:
        result.findings.append(Finding(
            layer="performance", severity="warn",
            message=f"Slow load: {result.latency_ms} ms",
        ))

    # --- screenshot --------------------------------------------------------
    try:
        settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
        shot = settings.artifacts_dir / f"{_url_safe_filename(url)}.png"
        page.screenshot(path=str(shot), full_page=False)
        result.screenshot_path = str(shot)
    except Exception as e:  # noqa: BLE001
        log.warning("screenshot failed for %s: %s", url, e)

    page.close()
    return result
