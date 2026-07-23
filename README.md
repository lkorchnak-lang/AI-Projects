# Altus Fit-Scoring Prototype

Implements the pipeline and rubric from the PRD (§6–7): Apollo ingestion → deterministic
firmographic signal resolution → 3x model research pass on content signals → deterministic
scoring, plausibility gate, and banding.

## What's real vs. what's stubbed

| Component | Status |
|---|---|
| `data/*.json` | Real seed data — capabilities, full 41-row signal mapping (weights, HPR tags, sources), references, org profile — matching the PRD exactly. |
| `src/signal_resolution.py` | Real, deterministic logic. Fully testable without network (see below). |
| `src/scoring_engine.py` | Real, deterministic logic — the core of the rubric. Fully tested via `--mock` runs. |
| `src/apollo_client.py` | Real code against Apollo's documented API shape, but **not live-tested** — this sandbox can't reach `api.apollo.io`. Confirm field names against Apollo's live docs before trusting it (flagged inline in the code and in PRD §10). |
| `src/research_agent.py` | Real code that calls the Claude API with the system prompt from PRD §6.7 and the web_search tool — but **not live-tested** here (no API key in this sandbox). |
| `main.py` / `src/pipeline.py` | Orchestration — real, and exercised end-to-end via mock mode. |

## Running it

### Option A — the app (recommended)

```bash
pip install -r requirements.txt
streamlit run app.py
```

Opens a browser UI at `http://localhost:8501` with:
- **Sidebar:** toggle Mock mode (no keys needed) or Live mode, where you paste your Apollo API key and Anthropic API key directly into the app — no environment variables required. Keys are held only in that browser session's memory; they are never written to disk by this app.
- **"Score a Prospect" tab:** score one company, see a results table (score, band, discount flag, rationale), expand the full JSON, download it.
- **"Validation Set" tab:** upload your filled-out `Altus_Validation_Set_Template.xlsx`, click "Run validation," and get a per-company comparison table plus two summary metrics — **top-1 capability match** (did the pipeline rank the partner's chosen capability #1?) and **band match** (did the predicted band for that capability match the partner's Fit Judgment?). Downloadable as CSV.

**Important:** this is a Phase 1 prototype UI, not a deployed app — it's a local Streamlit process with no auth, no hosting, and no persistence beyond the current browser tab. Treat it as a testing/validation tool for you and Altus partners to use locally, not something to hand out a public link to yet.

### Option B — CLI (for scripting/automation)

**Mock mode (no keys, no network — proves the scoring logic):**
```bash
python main.py --company "Acme Robotics" --mock spo_strong
python main.py --company "Globex Industrial" --mock mismatch_stage
```

**Live mode:**
```bash
export APOLLO_API_KEY="..."       # Altus's master key
export ANTHROPIC_API_KEY="..."
python main.py --company "Some Company" --domain somecompany.com
```

Two mock scenarios are built in (used by both the CLI and the app):
- `spo_strong`: a company that should score highest on Sales Process Optimization, and exercises the `fired_fraction` / `variance_note` mechanic (one signal deliberately fires in only 2 of 3 mock research runs).
- `mismatch_stage`: a company outside Altus's served industries, to confirm the plausibility discount fires and is flagged transparently in `plausibility_discount_reason`.

## The validation layer

`src/validation.py` loads real rows from the Validation Set spreadsheet and compares each
scored company's output against the partner's judgment:
- **`load_validation_rows(path)`** — parses the sheet, skipping the header, the example
  row, and any stray footer text (a real bug found while testing this: the sheet's "*
  Required field" footnote was initially being picked up as a bogus company row — fixed by
  requiring both a company name and a judged capability to count as real data).
- **`evaluate_row(row, scored_result)`** — returns top-1 match and band match for one company.
- **`summarize(evaluations)`** — aggregate accuracy across the whole set.

The app's "Validation Set" tab wraps these three functions; you can also call them
directly from a script if you want a different report format.

## What was found and fixed while building this

Worth knowing before you extend this: a signal can map to multiple capabilities (e.g. "No
clear CTA" maps to both MDB and SPO at different weights). The first pass of this pipeline
deduped research signals by `(signal_name, capability_code)`, which meant the same
real-world fact got judged and counted once per capability instead of once per company —
inflating `fired_fraction` past 1.0. Fixed by deduping by `signal_name` alone (a signal is
one fact regardless of how many capabilities reference it) and making the aggregator in
`scoring_engine.py` defensive against duplicates regardless. Covered by the `spo_strong`
mock run, which exercises a signal shared across 3 capabilities.

Also found: Apollo returns raw stage strings (e.g. `"series_b"`, `"public"`) that won't
match `org_profile`'s standardized `company_stages_served` vocabulary verbatim. Added
`normalize_stage()` in `src/pipeline.py` as a first-pass mapping — extend it once you see
real Apollo response values, since it's currently guessed, not verified.

## What this does NOT include yet

- The validation layer (PRD §8) — running real prospects through this and checking output
  against partner judgment. This was intentionally deferred; wire it in once the
  `Validation_Set_Template.xlsx` sheet is filled out with real companies, by looping
  `score_company()` over each row and comparing `band`/`capability_code` to the partner's
  judgment column.
- Any UI. This is a CLI/library only, per Phase 1 scope.
- Live testing of `apollo_client.py` and `research_agent.py` — both need a real environment
  with network access and API keys, which this sandbox doesn't have.
- The 4th "rationale-writing" model call mentioned in PRD §7 step 6 — the current rationale
  is a plain deterministic string built from the scored data (see `scoring_engine.py`,
  `score_prospect()`). This is a reasonable placeholder for a prototype; swap in a light
  model call there once you want prose that reads more naturally for partners.
