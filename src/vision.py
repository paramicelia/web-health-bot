"""Vision layer — LLM judgement on ambiguous pages.

Called ONLY when the deterministic layer produced soft signals (warns
but no hard fail, or weird DOM shapes). The model gets:
  - a screenshot of the page
  - a summary of the deterministic signals we already collected
  - a strict prompt telling it what it is and isn't judging

Return is a VisionVerdict: status (ok / warn / fail), issues, confidence,
rationale, refused. Fail-open: if the vision layer errors, we keep the
deterministic verdict instead of blocking on it.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path

from .config import settings, vision_backend
from .schemas import Finding, VisionVerdict

log = logging.getLogger(__name__)


_SYSTEM = """You are a web-health inspector. You are shown a screenshot of a web page and a JSON summary of objective signals we already collected (HTTP status, broken resource count, title, body text length).

Decide if the page looks BROKEN to a human user. Return ONE verdict.

Judge ONLY:
- Visible error messages, stack traces, "Internal Server Error" banners, framework crash screens.
- Layout clearly broken: overlapping UI, massive empty regions, nothing visible above the fold.
- Missing images shown as broken-image icons, or placeholder graphics where real content is expected.
- Obvious under-construction / placeholder text ("Coming soon", "Lorem ipsum", "Example Domain" as default landing).
- Content that clearly belongs to a different site, or a 404/500 page styled to look like a 200.

Do NOT judge:
- Whether the design is pretty.
- Whether the content is useful or on-brand.
- Whether the page loads fast.
- Anything about user flows, forms, or behaviour you can't see.

If the screenshot is unreadable (blank, corrupted, nothing visible) or you genuinely cannot decide, set `refused: true` and status: "warn" — a human should look.

Output ONLY a JSON object:
{
  "status": "ok" | "warn" | "fail",
  "issues": ["<short phrase>", ...],
  "confidence": 1-5,
  "rationale": "<one sentence>",
  "refused": false
}

Status mapping:
- "ok"   — page looks normal.
- "warn" — something looks off but the page is usable.
- "fail" — page is clearly broken for an end user.
"""


def _read_image_b64(path: str) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def _findings_summary(findings: list[Finding]) -> str:
    return json.dumps([{
        "layer": f.layer, "severity": f.severity, "message": f.message,
    } for f in findings], indent=2)


def judge(screenshot_path: str, findings: list[Finding]) -> VisionVerdict:
    """Return a VisionVerdict. On any error, return a fail-open `refused` verdict."""
    backend = vision_backend()
    if backend == "none":
        return VisionVerdict(
            status="warn", confidence=1, refused=True,
            rationale="No LLM key configured; skipping vision check.",
        )

    try:
        img_b64 = _read_image_b64(screenshot_path)
    except Exception as e:  # noqa: BLE001
        return VisionVerdict(
            status="warn", confidence=1, refused=True,
            rationale=f"Could not read screenshot: {e!s}",
        )

    user_text = (
        "Deterministic signals gathered from the page:\n"
        + _findings_summary(findings)
        + "\n\nScreenshot attached. Judge per the rules and return the JSON verdict."
    )

    if backend == "groq":
        return _judge_groq(user_text, img_b64)
    return _judge_anthropic(user_text, img_b64)


# --- Groq vision -----------------------------------------------------------

def _judge_groq(user_text: str, img_b64: str) -> VisionVerdict:
    try:
        from groq import Groq
        client = Groq(api_key=settings.groq_api_key)
        # Groq's vision models accept image_url in the messages format with
        # a data:image/png;base64 payload.
        resp = client.chat.completions.create(
            model=settings.groq_model,
            temperature=0.1,
            max_tokens=512,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                ]},
            ],
        )
        raw = resp.choices[0].message.content or "{}"
        return _parse_verdict(raw)
    except Exception as e:  # noqa: BLE001
        log.warning("groq vision call failed: %s", e)
        return VisionVerdict(
            status="warn", confidence=1, refused=True,
            rationale=f"Vision backend error (groq): {str(e)[:140]}",
        )


# --- Anthropic vision ------------------------------------------------------

def _judge_anthropic(user_text: str, img_b64: str) -> VisionVerdict:
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=settings.anthropic_api_key)
        resp = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=512,
            temperature=0.1,
            system=_SYSTEM,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
                    {"type": "text", "text": user_text + "\n\nReturn ONLY the JSON verdict."},
                ],
            }],
        )
        raw = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        return _parse_verdict(raw)
    except Exception as e:  # noqa: BLE001
        log.warning("anthropic vision call failed: %s", e)
        return VisionVerdict(
            status="warn", confidence=1, refused=True,
            rationale=f"Vision backend error (anthropic): {str(e)[:140]}",
        )


# --- parsing ---------------------------------------------------------------

def _parse_verdict(raw: str) -> VisionVerdict:
    """Tolerant JSON parser — some models wrap output in fences."""
    try:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(m.group(0) if m else raw)
    except Exception as e:  # noqa: BLE001
        return VisionVerdict(
            status="warn", confidence=1, refused=True,
            rationale=f"Vision output not JSON: {str(e)[:140]}; raw: {raw[:120]!r}",
        )

    status = str(data.get("status", "warn")).lower()
    if status not in ("ok", "warn", "fail"):
        status = "warn"

    try:
        conf = int(data.get("confidence", 1))
    except (TypeError, ValueError):
        conf = 1
    conf = max(1, min(5, conf))

    issues = data.get("issues") or []
    if isinstance(issues, str):
        issues = [issues]
    issues = [str(i)[:200] for i in issues if i]

    return VisionVerdict(
        status=status,  # type: ignore[arg-type]
        issues=issues,
        confidence=conf,
        rationale=str(data.get("rationale", ""))[:400],
        refused=bool(data.get("refused", False)),
    )
