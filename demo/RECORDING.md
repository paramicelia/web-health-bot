# How to record the demo (25-35 seconds)

You have **two options** — pick one or do both:

- **(A) Web UI** (recommended for the GIF): browser viewer at `/`, each
  URL becomes a coloured card with screenshot thumbnail. Higher visual
  density, screenshots make it look like a tool, not a script.
- **(B) Terminal**: the rich-formatted CLI output. Lower visual density
  but emphasises the "LLM calls 4 (deterministic short-circuit saved 3)"
  summary line, which is the project's whole pitch.

The interesting visual in either path is the **boundary signal** — pages
that are obviously broken (404, 500) get a `FAIL` badge in milliseconds
with no LLM call, while ambiguous ones get the `LLM ✓` pill and a vision
verdict.

## Tool

Same as the RAG repo: **ShareX** with screen recording → GIF.
- Recording region: terminal window only (1280 × 800 fits the rich table
  without wrapping).
- FPS: 10–12.
- Save to: `web-health-bot/demo/demo.gif`.

If you want a typewriter-style intro (typing the command), use
**asciinema** (https://asciinema.org/) and convert to GIF with `agg`:
```bash
asciinema rec demo.cast
agg demo.cast demo.gif --speed 1.5
```

---

## Path A — Web UI demo (recommended)

### Setup

1. Start the API:
   ```bash
   uvicorn src.api:app --host 127.0.0.1 --port 8000 --log-level warning
   ```
2. Open `http://127.0.0.1:8000/` in a clean browser window.
3. Click **Load defaults** once — `targets.yaml` URLs fill the textarea.
4. Run once before recording so Playwright/Groq are warm.

### Script (~30 sec)

| # | Action                                                       | Why                                                      |
|---|--------------------------------------------------------------|----------------------------------------------------------|
| 1 | Show the populated textarea + the backend badge ("groq")     | Sets the scene                                           |
| 2 | Click **Run health check**                                   | Cards start streaming in                                 |
| 3 | Pause as `example.com` lands `OK` with `LLM skipped` pill    | Boundary working: green page = no LLM call               |
| 4 | Pause as `status/500` lands `FAIL` with `LLM skipped` pill   | Boundary working: hard fail = no LLM call                |
| 5 | Pause as `broken_images` lands `FAIL` with `LLM ✓` pill      | LLM called only when needed; screenshot thumbnail visible |
| 6 | Show summary stats at top — total, ok, warn, fail, LLM saved | The money frame                                          |

What to highlight visually: the **`LLM ✓` vs `LLM skipped`** pills are
the entire project. Mouse-hover over the screenshot thumbnail of
`broken_images` — visible broken-image icons explain why vision said
fail.

---

## Path B — Terminal demo

### Setup

1. Open Windows Terminal at the project root, full screen.
2. Set font size to ~14pt so the table is readable in the GIF after
   resize.
3. Pre-run once to:
   - Warm up the Playwright browser binary (first run is slow)
   - Make sure all target URLs are reachable from your network
   - Confirm `report.json` writes cleanly
   ```bash
   python check.py
   ```
4. Clear the terminal: `clear` (or `cls`).
5. Start recording.

---

## The 4-shot script (≈ 30 seconds total)

| # | Action                                            | Why                                                             | ~time |
|---|---------------------------------------------------|-----------------------------------------------------------------|-------|
| 1 | Type `python check.py` (or paste)                 | Sets the scene: it's a CLI, takes no args, uses `targets.yaml`  | 2 s   |
| 2 | Hit Enter — let logs scroll                        | Shows it's actually walking pages (Playwright requests visible) | 12 s  |
| 3 | When the rich table renders — pause cursor on the **Summary** block | Money frame: `LLM calls 4 (deterministic short-circuit saved 3)` is the whole point | 6 s   |
| 4 | Scroll up to one of the per-page findings tables  | Shows that every signal is tagged with layer + severity         | 5 s   |
| 5 | (optional) `cat report.json | head -40` or open it in editor | Shows JSON output is structured, not just printed             | 5 s   |

If you want a second clip, run
```bash
python check.py --no-vision https://example.com https://httpbin.org/status/500
```
to show the deterministic-only mode. Useful if a reviewer doesn't have
an API key.

---

## What to highlight visually

- **The colour coding.** Red FAIL on httpbin/status/500, yellow WARN on
  the herokuapp broken-images page, green OK on example.com.
- **The "LLM?" column.** No on hard fails (404, 500, example.com), yes
  on the warn rows. That column alone explains the boundary policy.
- **The Summary line.** "LLM calls 4 (deterministic short-circuit saved
  3)" — this is what you want a reviewer to remember.

---

## Bonus: a screenshot for the README poster

After the run, drop a single screenshot of the rich table into
`demo/poster.png` and reference it in the README before the GIF — gives
a static fallback when GitHub doesn't autoplay or the GIF is loading.

---

## After recording

```bash
mv path/to/sharex-output.gif demo/demo.gif
git add demo/demo.gif
git commit -m "Add terminal demo gif"
git push
```

Then uncomment the `<!-- ![Demo](demo/demo.gif) -->` line in `README.md`.
