"""Orchestrator — where the boundary between deterministic and LLM lives.

The core decision this file makes is: *for this page, do we need the
vision LLM at all?*

  - Hard deterministic FAIL (HTTP 5xx, navigation timeout, many broken
    resources) → overall=fail. Skip the LLM. Answer is obvious, calling
    a model would only add latency, cost, and a chance of a wrong
    override.
  - All deterministic green (HTTP 2xx + title + enough text + no broken
    resources + no console errors + fast) → overall=ok. Skip the LLM.
    We have no reason to spend tokens just to agree.
  - Otherwise → ambiguous. Hand the screenshot + signals to the vision
    layer. The LLM may escalate (warn→fail) or downgrade (warn→ok) but
    it **cannot override a hard deterministic fail** (we never get here
    in that case) and it **cannot promote a warn to ok with low
    confidence** — that's the conservative rule below.

Why this ordering: the deterministic layer is cheap, fast, and
explainable. The LLM is none of those. Use it where it uniquely adds
value: subjective judgement on a screenshot ("does this look broken").
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from playwright.sync_api import Browser

from . import deterministic, vision
from .config import vision_backend
from .schemas import Finding, PageReport, Severity, VisionVerdict

log = logging.getLogger(__name__)


_SEV_RANK = {"ok": 0, "warn": 1, "fail": 2}


def _worst(severities: list[Severity]) -> Severity:
    return max(severities, key=lambda s: _SEV_RANK[s]) if severities else "ok"


@dataclass
class Decision:
    overall: Severity
    needed_llm: bool
    reason: str
    vision_verdict: VisionVerdict | None = None


def decide(findings: list[Finding], screenshot_path: str | None) -> Decision:
    """Apply the boundary policy."""
    det_worst = _worst([f.severity for f in findings])

    # --- Rule 1: hard deterministic fail → short-circuit -------------------
    if det_worst == "fail":
        msg = next((f.message for f in findings if f.severity == "fail"), "deterministic fail")
        return Decision(overall="fail", needed_llm=False, reason=msg)

    # --- Rule 2: no ambiguity at all → short-circuit -----------------------
    if det_worst == "ok":
        return Decision(overall="ok", needed_llm=False, reason="all deterministic signals green")

    # --- Rule 3: ambiguous → ask the vision layer --------------------------
    if not screenshot_path:
        return Decision(
            overall="warn", needed_llm=False,
            reason="ambiguous signals and no screenshot to judge",
        )

    verdict = vision.judge(screenshot_path, findings)
    if verdict.refused:
        # Vision refused (low-quality screenshot, model error). Keep the
        # deterministic verdict — that's warn here by construction.
        return Decision(
            overall="warn", needed_llm=True, vision_verdict=verdict,
            reason=f"deterministic warns; vision refused: {verdict.rationale[:120]}",
        )

    # Escalation: vision can promote warn→fail, or confirm warn→warn.
    # It can also downgrade warn→ok — but only with high confidence (>=4).
    # That conservative rule prevents a single LLM call from silencing
    # real deterministic warnings on a low-confidence "looks fine" read.
    if verdict.status == "fail":
        final = "fail"
    elif verdict.status == "ok" and verdict.confidence >= 4:
        final = "ok"
    else:
        final = "warn"

    return Decision(
        overall=final, needed_llm=True, vision_verdict=verdict,
        reason=verdict.rationale or f"vision: {verdict.status} (conf {verdict.confidence})",
    )


def check_page(browser: Browser, url: str) -> PageReport:
    t0 = time.perf_counter()
    probe = deterministic.probe(browser, url)
    total_ms = int((time.perf_counter() - t0) * 1000)

    if probe.error:
        # Navigation itself failed — deterministic verdict is already fail.
        return PageReport(
            url=url, overall="fail",
            reason=probe.error,
            latency_ms=total_ms,
            needed_llm=False,
            findings=probe.findings,
            screenshot_path=probe.screenshot_path,
            error=probe.error,
        )

    decision = decide(probe.findings, probe.screenshot_path)
    # If vision ran, capture its verdict as a finding too so the report
    # renders everything uniformly.
    extended = list(probe.findings)
    if decision.vision_verdict is not None:
        v = decision.vision_verdict
        extended.append(Finding(
            layer="vision",
            severity=v.status,
            message=(v.rationale or f"vision: {v.status}")[:200],
            detail={"issues": v.issues, "confidence": v.confidence, "refused": v.refused},
        ))

    return PageReport(
        url=url,
        overall=decision.overall,
        reason=decision.reason,
        latency_ms=total_ms,
        needed_llm=decision.needed_llm,
        findings=extended,
        vision=decision.vision_verdict,
        screenshot_path=probe.screenshot_path,
    )


def backend_label() -> str:
    return vision_backend()
