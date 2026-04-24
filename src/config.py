"""Runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    # --- browser ---
    headless: bool = os.getenv("HEADLESS", "true").lower() != "false"
    navigation_timeout_ms: int = int(os.getenv("NAVIGATION_TIMEOUT_MS", "15000"))

    # --- deterministic thresholds ---
    slow_threshold_ms: int = int(os.getenv("SLOW_THRESHOLD_MS", "8000"))
    very_slow_threshold_ms: int = int(os.getenv("VERY_SLOW_THRESHOLD_MS", "15000"))
    min_text_length: int = int(os.getenv("MIN_TEXT_LENGTH", "120"))
    max_broken_resources_warn: int = 2   # > this → warn
    max_broken_resources_fail: int = 10  # > this → fail

    # --- llm ---
    groq_api_key: str | None = os.getenv("GROQ_API_KEY") or None
    anthropic_api_key: str | None = os.getenv("ANTHROPIC_API_KEY") or None
    groq_model: str = os.getenv("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

    # --- paths ---
    artifacts_dir: Path = ROOT / os.getenv("ARTIFACTS_DIR", "artifacts").removeprefix("./")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()


def vision_backend() -> str:
    if settings.groq_api_key:
        return "groq"
    if settings.anthropic_api_key:
        return "anthropic"
    return "none"  # deterministic-only mode
