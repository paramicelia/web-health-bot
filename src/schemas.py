"""Pydantic models — the contract of the health report.

Findings are the atomic units. A PageReport aggregates findings from the
deterministic probe and (optionally) the vision layer into a single
overall severity.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["ok", "warn", "fail"]
Layer = Literal["http", "resources", "console", "dom", "performance", "vision", "navigation"]


class Finding(BaseModel):
    layer: Layer
    severity: Severity
    message: str
    detail: dict = Field(default_factory=dict)


class VisionVerdict(BaseModel):
    status: Severity                # ok | warn (degraded) | fail (broken)
    issues: list[str] = Field(default_factory=list)
    confidence: int = Field(ge=1, le=5)
    rationale: str = ""
    refused: bool = False           # model couldn't judge from screenshot alone


class PageReport(BaseModel):
    url: str
    overall: Severity
    reason: str                     # short human sentence for the report
    latency_ms: int
    needed_llm: bool                # did we call the vision layer?
    findings: list[Finding] = Field(default_factory=list)
    vision: VisionVerdict | None = None
    screenshot_path: str | None = None
    error: str | None = None        # if the whole probe failed (DNS, timeout)


class RunReport(BaseModel):
    generated_at: str
    vision_backend: str
    pages: list[PageReport]
    total_llm_calls: int
    total_pages: int
