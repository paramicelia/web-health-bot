# Web Health Bot

> A bot that walks a list of public pages and reports which ones look
> broken. The interesting design question isn't *how* to check a page —
> it's **deciding which checks to trust a program with and which to ask
> an LLM**. This repo is about that boundary.

Built as test task #1 for the Growe AI Specialist role.

---

## The boundary problem

Most "is the page OK?" checks are objective:

- HTTP 500 → broken. No question.
- HTTP 200 and the page has content → probably fine.
- 12 broken resource requests → almost certainly broken.

Calling an LLM to confirm any of that is pure waste — slower, costlier,
and occasionally wrong. But some judgements are genuinely subjective:

- The page returns 200 but shows *"Internal Server Error"* inside a
  styled wrapper.
- The layout is so broken that no one could actually use it.
- Everything loaded, but the content is lorem-ipsum placeholder.

A deterministic program can't decide those. An LLM looking at the
screenshot can.

The goal of this bot isn't to maximise coverage of either layer — it's
to draw a disciplined line between them. Three rules:

1. **Hard deterministic FAIL → skip the LLM.** Navigation timeout, 4xx,
   5xx, too many broken resources. We already know. Calling the model
   only adds latency and risks a wrong override.

2. **All deterministic signals green → skip the LLM.** 200 OK + title +
   body text + no broken resources + not slow. There's nothing for the
   model to add. Don't spend tokens to agree with yourself.

3. **Ambiguous (warns without hard fail) → ask the LLM.** This is the
   only place the vision model is used. It gets the screenshot plus the
   deterministic signals we collected, and returns `ok | warn | fail`
   with a confidence score.

### Two extra rules make the LLM honest

- **It cannot override a hard deterministic fail** — we never call it
  in that case. HTTP 500 stays HTTP 500 regardless of how polished the
  page looks in the screenshot.
- **It cannot silence a deterministic warn with low confidence.** If
  the model says "looks fine" with confidence ≤ 3, the verdict stays at
  `warn`. Only a high-confidence (≥ 4) `ok` downgrades the verdict.

These two rules are the reason the LLM is a useful tool here and not a
risk surface. Both are covered by unit tests (`tests/test_boundary.py`).

---

## Quick start

```bash
# install (Playwright needs a chromium install the first time)
pip install -r requirements.txt
python -m playwright install chromium

# configure the vision backend (optional but recommended)
cp .env.example .env
# set GROQ_API_KEY=... or ANTHROPIC_API_KEY=...

# run against the default targets.yaml
python check.py

# or ad-hoc
python check.py https://example.com https://httpbin.org/status/500

# deterministic-only (no LLM at all)
python check.py --no-vision
```

Output: a rich table + per-page findings breakdown + a `report.json`
file with the full structured results.

---

## Pipeline

```
                     url
                      │
                      ▼
          ┌───────────────────────┐
          │ Playwright navigate   │───► DNS/TLS/timeout → FAIL
          │ + gather signals      │
          └──────────┬────────────┘
                     │
           http · resources · console · dom · performance
                     │
                     ▼
          ┌───────────────────────┐
          │   Boundary rules      │
          │                       │
          │   any hard FAIL?      │──► FAIL (LLM skipped)
          │   all OK?             │──► OK   (LLM skipped)
          │   else                │──► vision LLM
          └──────────┬────────────┘
                     │
                     ▼
          ┌───────────────────────┐
          │  Vision judgement     │
          │  (screenshot + signals)│
          │  → ok / warn / fail    │
          └──────────┬────────────┘
                     │ merge with rule constraints
                     ▼
                PageReport
          (overall, reason, findings,
           screenshot_path, trace of
           which checks fired)
```

---

## Deterministic signals

Each is converted to at most one `Finding` with a severity:

| Signal               | Severity                                                  |
|----------------------|-----------------------------------------------------------|
| HTTP 2xx             | `ok`                                                      |
| HTTP 3xx (unresolved) | `warn` (page load wasn't followed to its terminal state) |
| HTTP 4xx / 5xx       | `fail`                                                    |
| Navigation timeout or exception | `fail`                                         |
| Broken sub-resources > 2 / > 10 | `warn` / `fail`                                |
| Console errors > 3   | `warn`                                                    |
| Missing `<title>`    | `warn`                                                    |
| Body text < 120 chars | `warn` (might be an SPA shell, might be blank)           |
| Load > 8s / > 15s    | `warn`                                                    |

Thresholds in `.env.example` (`SLOW_THRESHOLD_MS`,
`MIN_TEXT_LENGTH`, etc.) are starting points, not tuned optima.

---

## Vision prompt (excerpt)

```
You are a web-health inspector. You are shown a screenshot of a web page
and a JSON summary of the objective signals we already collected.

Judge ONLY:
- Visible error messages, stack traces, framework crash screens.
- Layout clearly broken: overlapping UI, massive empty regions.
- Missing images, placeholder graphics where real content is expected.
- Obvious under-construction / placeholder text.
- Content that clearly belongs to a different site, or a 404/500 page
  styled to look like a 200.

Do NOT judge:
- Whether the design is pretty.
- Whether the content is useful or on-brand.
- Whether the page loads fast.

If you genuinely cannot decide, set `refused: true` — a human should look.
```

Full prompt in `src/vision.py`. The "do NOT judge" block is the
discipline that keeps the LLM from opining about taste.

---

## Sample run

```
Web Health Report
 url                                                 status   latency   LLM?  reason
 ───────────────────────────────────────────────────────────────────────────────────
 https://example.com                                   OK       604 ms   no   all deterministic signals green
 https://httpbin.org/html                              WARN     838 ms   yes  Page has no <title> — vision: literary work, no visible errors
 https://httpbin.org/status/404                        FAIL     410 ms   no   HTTP 404 — client error
 https://httpbin.org/status/500                        FAIL     393 ms   no   HTTP 500 — server error
 https://the-internet.herokuapp.com/broken_images      WARN    1204 ms   yes  broken image icons + short body text
 https://the-internet.herokuapp.com/status_codes/301   WARN     982 ms   yes  HTTP 301 — redirect not resolved
 https://httpbin.org/html?short                        WARN     697 ms   yes  Page has no <title>

Summary
  vision backend         groq
  pages checked          7
  ok                     1
  warn                   4
  fail                   2
  LLM calls              4 (deterministic short-circuit saved 3)
```

Note the last line — for 3 of 7 pages the deterministic rules short-
circuited the LLM entirely. That's the whole point.

---

## Structure

```
web-health-bot/
├── README.md                ← this file
├── requirements.txt
├── .env.example
├── targets.yaml             ← list of URLs to check
├── check.py                 ← CLI entry
├── report.json              ← generated per run
├── artifacts/               ← per-page PNG screenshots (LLM input)
├── src/
│   ├── config.py            ← thresholds + backend selection
│   ├── schemas.py           ← Finding, PageReport, VisionVerdict
│   ├── deterministic.py     ← Playwright probe — HTTP, DOM, console, timing
│   ├── vision.py            ← LLM vision (Groq or Anthropic)
│   ├── bot.py               ← the orchestrator + boundary rules
│   └── reporter.py          ← rich table + JSON report writer
└── tests/
    └── test_boundary.py     ← 8 tests pinning the boundary policy
```

---

## What I'd improve (honest)

- **Multi-page crawling.** Right now you pass a flat list of URLs. A
  real monitor would crawl from a seed and follow internal links up to
  a depth, so an issue on a sub-page is noticed without being listed
  explicitly.
- **Stable per-page baselines.** The LLM currently has no memory of
  what "normal" looks like for each page. Storing a known-good
  screenshot and comparing (either pixel-diff or LLM-as-judge) would
  catch regressions that look fine in isolation.
- **Parallelisation.** Pages are checked serially. Playwright supports
  multiple contexts; a bounded worker pool would cut a 7-page run from
  ~8s to ~2s.
- **Auth / cookie support.** Nothing behind a login works right now.
- **Scheduled mode.** A cron-style loop that writes to a durable
  store and posts a diff to Slack when a page flips status is one
  evening of work on top of this.
- **Prometheus metrics.** `llm_calls_total`, `pages_failed_total`,
  `vision_verdict_total{status="fail"}` — trivial to add, high value
  for an ops team.
- **Threshold auto-tuning.** Latency thresholds are global. Per-page
  expected latency would let you flag "this page is 3× slower than
  usual" without needing a firm number.
- **Safer vision output contract.** Right now the vision layer returns
  free-form `issues` strings. A typed taxonomy
  (`missing_images | layout_collapse | error_banner | placeholder`)
  would make reporting and aggregation much better.

---

## Design principles this project commits to

- **Cheap checks first, always.** Never pay a model call when a
  regex / HTTP code / element selector can give a clear answer.
- **The LLM is a narrow tool, not a general judge.** Its prompt tells
  it what it is and isn't judging. Anything off-contract goes to a
  human.
- **Conservative escalation, permissive de-escalation.** The LLM can
  turn a warn into a fail freely, but can only downgrade a warn to OK
  with high confidence. One-sided rules make the bot safer to run
  unattended.
- **Every finding is auditable.** The JSON report has every signal
  with layer, severity, and evidence — no opaque aggregate scores.
