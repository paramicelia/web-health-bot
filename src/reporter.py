"""Human-readable reporter. Prints a rich table and writes a JSON report."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from rich import box
from rich.console import Console
from rich.table import Table

from .schemas import PageReport, RunReport


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


_SEV_COLOUR = {"ok": "green", "warn": "yellow", "fail": "red"}
_SEV_TAG = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}


def print_report(reports: list[PageReport], backend: str, console: Console | None = None) -> None:
    console = console or Console()

    t = Table(title="Web Health Report", box=box.SIMPLE_HEAVY, show_lines=False)
    t.add_column("url", overflow="fold", max_width=48)
    t.add_column("status", justify="center")
    t.add_column("latency", justify="right")
    t.add_column("LLM?", justify="center")
    t.add_column("reason", overflow="fold", max_width=60)

    for r in reports:
        colour = _SEV_COLOUR[r.overall]
        tag = f"[bold {colour}]{_SEV_TAG[r.overall]}[/bold {colour}]"
        t.add_row(
            r.url,
            tag,
            f"{r.latency_ms} ms",
            "yes" if r.needed_llm else "no",
            r.reason[:120],
        )
    console.print(t)

    # Summary
    n = len(reports)
    n_ok = sum(1 for r in reports if r.overall == "ok")
    n_warn = sum(1 for r in reports if r.overall == "warn")
    n_fail = sum(1 for r in reports if r.overall == "fail")
    n_llm = sum(1 for r in reports if r.needed_llm)
    saved = n - n_llm

    summary = Table(title="Summary", box=box.SIMPLE, show_header=False)
    summary.add_column("key"); summary.add_column("value", justify="right")
    summary.add_row("vision backend", backend)
    summary.add_row("pages checked", str(n))
    summary.add_row("ok", f"[green]{n_ok}[/green]")
    summary.add_row("warn", f"[yellow]{n_warn}[/yellow]")
    summary.add_row("fail", f"[red]{n_fail}[/red]")
    summary.add_row("LLM calls", f"{n_llm} (deterministic short-circuit saved {saved})")
    console.print(summary)

    # Per-page findings detail
    for r in reports:
        if not r.findings:
            continue
        pt = Table(title=f"{r.url}", box=box.MINIMAL, show_lines=False, title_justify="left")
        pt.add_column("layer"); pt.add_column("sev"); pt.add_column("message", overflow="fold", max_width=90)
        for f in r.findings:
            colour = _SEV_COLOUR[f.severity]
            pt.add_row(f.layer, f"[{colour}]{_SEV_TAG[f.severity]}[/{colour}]", f.message)
        console.print(pt)


def write_json(reports: list[PageReport], backend: str, path: Path) -> None:
    run = RunReport(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        vision_backend=backend,
        pages=reports,
        total_llm_calls=sum(1 for r in reports if r.needed_llm),
        total_pages=len(reports),
    )
    path.write_text(run.model_dump_json(indent=2), encoding="utf-8")
