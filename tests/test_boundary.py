"""Unit tests for the orchestrator boundary policy.

The boundary is the single most important decision in this project — a
wrong call here is either a wasted LLM token (expensive / slow) or a
missed escalation (bad). Pin it down with tests so future edits can't
silently shift the policy.

These tests stub out the vision module so they run offline and
deterministically.
"""

from __future__ import annotations

import pytest

from src import bot
from src.schemas import Finding, VisionVerdict


# --- test doubles ---------------------------------------------------------

class FakeVision:
    """Replaces src.vision.judge with a scripted response."""

    def __init__(self, verdict: VisionVerdict | None = None) -> None:
        self.verdict = verdict
        self.calls = 0

    def __call__(self, screenshot_path: str, findings: list[Finding]) -> VisionVerdict:
        self.calls += 1
        if self.verdict is None:
            raise AssertionError("FakeVision was called but no verdict scripted")
        return self.verdict


@pytest.fixture(autouse=True)
def _patch_vision(monkeypatch):
    """Default: vision raises if called. Each test that needs it overrides."""
    fv = FakeVision()
    monkeypatch.setattr(bot.vision, "judge", fv)
    return fv


# --- Rule 1: hard deterministic fail → LLM skipped ------------------------

def test_http_500_short_circuits_no_llm(_patch_vision):
    findings = [
        Finding(layer="http", severity="fail", message="HTTP 500 — server error"),
    ]
    d = bot.decide(findings, screenshot_path="/fake.png")
    assert d.overall == "fail"
    assert d.needed_llm is False
    assert _patch_vision.calls == 0


def test_navigation_timeout_short_circuits_no_llm(_patch_vision):
    findings = [
        Finding(layer="navigation", severity="fail", message="Timed out"),
    ]
    d = bot.decide(findings, screenshot_path=None)
    assert d.overall == "fail"
    assert d.needed_llm is False
    assert _patch_vision.calls == 0


# --- Rule 2: all-green deterministic → LLM skipped ------------------------

def test_all_green_short_circuits_no_llm(_patch_vision):
    findings = [
        Finding(layer="http", severity="ok", message="HTTP 200"),
    ]
    d = bot.decide(findings, screenshot_path="/fake.png")
    assert d.overall == "ok"
    assert d.needed_llm is False
    assert _patch_vision.calls == 0


# --- Rule 3: ambiguous → LLM is called ------------------------------------

def test_warn_only_invokes_vision(monkeypatch):
    fv = FakeVision(VisionVerdict(status="ok", confidence=5, rationale="looks fine"))
    monkeypatch.setattr(bot.vision, "judge", fv)

    findings = [
        Finding(layer="http", severity="ok", message="HTTP 200"),
        Finding(layer="dom", severity="warn", message="body text very short"),
    ]
    d = bot.decide(findings, screenshot_path="/fake.png")
    assert fv.calls == 1
    # Vision says ok with high confidence → downgrade to ok
    assert d.overall == "ok"
    assert d.needed_llm is True


def test_low_confidence_ok_cannot_silence_deterministic_warn(monkeypatch):
    fv = FakeVision(VisionVerdict(status="ok", confidence=2, rationale="maybe fine"))
    monkeypatch.setattr(bot.vision, "judge", fv)

    findings = [
        Finding(layer="http", severity="ok", message="HTTP 200"),
        Finding(layer="dom", severity="warn", message="body text very short"),
    ]
    d = bot.decide(findings, screenshot_path="/fake.png")
    assert d.overall == "warn"
    assert d.needed_llm is True


def test_vision_can_escalate_warn_to_fail(monkeypatch):
    fv = FakeVision(VisionVerdict(status="fail", confidence=4, rationale="broken layout"))
    monkeypatch.setattr(bot.vision, "judge", fv)

    findings = [
        Finding(layer="http", severity="ok", message="HTTP 200"),
        Finding(layer="dom", severity="warn", message="body text very short"),
    ]
    d = bot.decide(findings, screenshot_path="/fake.png")
    assert d.overall == "fail"


def test_vision_refused_keeps_warn(monkeypatch):
    fv = FakeVision(VisionVerdict(
        status="warn", confidence=1, refused=True, rationale="screenshot unreadable",
    ))
    monkeypatch.setattr(bot.vision, "judge", fv)

    findings = [
        Finding(layer="dom", severity="warn", message="body text very short"),
    ]
    d = bot.decide(findings, screenshot_path="/fake.png")
    assert d.overall == "warn"
    assert d.needed_llm is True


# --- Guardrail: no screenshot, ambiguous → stays warn, no LLM -------------

def test_ambiguous_without_screenshot_stays_warn(_patch_vision):
    findings = [
        Finding(layer="dom", severity="warn", message="very short body"),
    ]
    d = bot.decide(findings, screenshot_path=None)
    assert d.overall == "warn"
    assert d.needed_llm is False
    assert _patch_vision.calls == 0
