"""
Orchestrates the full flow (PRD SS7):
  1. Ingest company via Apollo
  2. Resolve Apollo-tagged signals deterministically
  3. Run the model 3x on Research / Apollo+Research signals
  4. Aggregate + score deterministically
  5. Return the final record (PRD SS6.8 schema)
"""
import json
import os

from src.apollo_client import ApolloClient, get_mock_organization
from src.signal_resolution import resolve_apollo_signals
from src.research_agent import run_research_pass, get_mock_research_pass
from src.scoring_engine import score_prospect

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

# Apollo returns raw strings ("series_b", "ipo", "public", etc.) that won't match the
# standardized company_stages_served vocabulary in org_profile.json verbatim. This map
# normalizes the common cases; extend it once you see real Apollo response values.
# NOTE: this is a best-effort first pass, not verified against live Apollo output.
STAGE_NORMALIZATION = {
    "seed": "Pre-Seed/Seed",
    "pre-seed": "Pre-Seed/Seed",
    "series a": "Series A",
    "series b": "Series B",
    "series c": "Series C+",
    "series d": "Series C+",
    "series e": "Series C+",
    "growth": "Growth/Late-Stage",
    "private equity": "PE-Backed/Portfolio Company",
    "public": "Public/Enterprise",
    "ipo": "Public/Enterprise",
}


def normalize_stage(raw_stage: str) -> str:
    if not raw_stage:
        return raw_stage
    return STAGE_NORMALIZATION.get(raw_stage.strip().lower(), raw_stage)


def load_seed_data():
    with open(os.path.join(DATA_DIR, "capabilities.json")) as f:
        capabilities = json.load(f)
    with open(os.path.join(DATA_DIR, "signal_mapping.json")) as f:
        signal_mapping = json.load(f)
    with open(os.path.join(DATA_DIR, "references.json")) as f:
        references = json.load(f)
    with open(os.path.join(DATA_DIR, "org_profile.json")) as f:
        org_profile = json.load(f)
    return capabilities, signal_mapping, references, org_profile


def score_company(
    company_name: str,
    domain: str = None,
    mock_scenario: str = None,
    num_runs: int = 3,
    apollo_api_key: str = None,
    anthropic_api_key: str = None,
) -> dict:
    """
    mock_scenario: if set (e.g. "spo_strong"), uses synthetic Apollo + research data
    instead of live calls -- for local testing without network access or API keys.

    apollo_api_key / anthropic_api_key: pass explicitly (e.g. from a UI input field) to
    override the APOLLO_API_KEY / ANTHROPIC_API_KEY environment variables. Falls back to
    the env vars if not provided.
    """
    capabilities, signal_mapping, references, org_profile = load_seed_data()

    # Step 1: Apollo ingestion
    if mock_scenario:
        apollo_org = get_mock_organization(mock_scenario)
    else:
        client = ApolloClient(api_key=apollo_api_key)
        apollo_org = client.get_organization(domain=domain)

    # Step 2: deterministic Apollo signal resolution
    apollo_signals = resolve_apollo_signals(apollo_org)

    # Step 3: model research pass, 3x, on Research / Apollo+Research signals only.
    # Dedupe by signal_name ONLY (not signal_name+capability_code): a signal is one
    # real-world fact even when it maps to multiple capabilities at different weights
    # (e.g. "No clear CTA" maps to both MDB and SPO) -- it should be researched/judged
    # once per company, not once per capability, or fired_fraction inflates past 1.0.
    signals_needing_research = [
        row for row in signal_mapping if row["data_source"] in ("Research", "Apollo + Research")
    ]
    seen_names = set()
    unique_signals_needing_research = []
    for row in signals_needing_research:
        if row["signal_name"] not in seen_names:
            seen_names.add(row["signal_name"])
            all_caps_for_signal = sorted({
                r["capability_code"] for r in signal_mapping if r["signal_name"] == row["signal_name"]
            })
            merged_row = dict(row)
            merged_row["capability_code"] = ", ".join(all_caps_for_signal)
            unique_signals_needing_research.append(merged_row)

    research_runs = []
    for run_number in range(1, num_runs + 1):
        if mock_scenario:
            run_result = get_mock_research_pass(company_name, run_number, unique_signals_needing_research)
        else:
            run_result = run_research_pass(
                company_name=company_name,
                domain=domain or apollo_org.get("domain"),
                apollo_context=apollo_org,
                signals_to_research=unique_signals_needing_research,
                run_number=run_number,
                anthropic_api_key=anthropic_api_key,
            )
        research_runs.append(run_result)

    # Step 4 + 5: deterministic scoring
    result = score_prospect(
        company_name=company_name,
        company_industry=apollo_org.get("industry"),
        company_stage=normalize_stage(apollo_org.get("funding_stage")),
        apollo_signals=apollo_signals,
        research_runs=research_runs,
        capabilities=capabilities,
        signal_mapping=signal_mapping,
        references=references,
        org_profile=org_profile,
    )
    return result
