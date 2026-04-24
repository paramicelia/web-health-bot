"""CLI entry point.

  python check.py                       # uses targets.yaml
  python check.py https://foo.com …     # ad-hoc URL(s)
  python check.py --no-vision           # deterministic-only, skip LLM even if key present
  python check.py --report out.json     # where to write JSON (default: report.json)
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import yaml

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright

from src import bot, reporter
from src.config import settings, vision_backend

ROOT = Path(__file__).resolve().parent


def load_targets(path: Path) -> list[str]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    urls: list[str] = []
    for item in data or []:
        if isinstance(item, str):
            urls.append(item)
        elif isinstance(item, dict) and "url" in item:
            urls.append(item["url"])
    if not urls:
        raise ValueError(f"No urls in {path}")
    return urls


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="*", help="URLs to check (overrides targets.yaml)")
    ap.add_argument("--targets", default=str(ROOT / "targets.yaml"))
    ap.add_argument("--report", default=str(ROOT / "report.json"))
    ap.add_argument("--no-vision", action="store_true",
                    help="Disable the vision layer (deterministic-only mode).")
    args = ap.parse_args()

    logging.basicConfig(level=settings.log_level,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("check")

    urls = args.urls or load_targets(Path(args.targets))
    backend = "none" if args.no_vision else vision_backend()

    # If --no-vision was requested, patch the config for this run. We do
    # it by monkey-patching vision_backend inside the bot module for this
    # process — simpler than rewiring config.
    if args.no_vision:
        import src.bot as b
        b.vision_backend = lambda: "none"  # type: ignore[assignment]
        import src.vision as v
        v.vision_backend = lambda: "none"  # type: ignore[assignment]

    log.info("Checking %d page(s) with vision backend=%s", len(urls), backend)

    reports = []
    t0 = time.perf_counter()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=settings.headless)
        try:
            for url in urls:
                log.info("→ %s", url)
                reports.append(bot.check_page(browser, url))
        finally:
            browser.close()
    elapsed = int((time.perf_counter() - t0) * 1000)
    log.info("Done in %d ms", elapsed)

    reporter.print_report(reports, backend)
    reporter.write_json(reports, backend, Path(args.report))
    print(f"\nFull report written to {args.report}")

    # Non-zero exit on any fail — useful for CI / scheduled runs.
    return 1 if any(r.overall == "fail" for r in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
