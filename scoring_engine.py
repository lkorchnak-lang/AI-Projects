"""
All arithmetic lives here, and only here. The model never computes a score (PRD SS6.3) --
it only judges whether signals fired. This module takes those judgments (deterministic
Apollo signals + averaged research-run signals) and produces the final scored record.
Given the same fired_fraction inputs, this module always produces the same output.
"""
from collections import defaultdict
from datetime import datetime, timezone

PLAUSIBILITY_DISCOUNT = 0.7

BAND_THRESHOLDS = [
    ("Strong Fit", 80),
    ("Probable Fit", 60),
    ("Possible Fit", 40),
]


def aggregate_fired_fractions(apollo_signals: dict, research_runs: list) -> dict:
    """
    apollo_signals: {signal_name: bool} from signal_resolution.py (identical every run
                    by construction -- contributes fired_fraction of 0.0 or 1.0).
    research_runs: list of 3 research-pass results (each from research_agent.py), each
                    containing "signals_evaluated": [{signal_name, fired, ...}, ...]

    Returns {signal_name: fired_fraction} for every signal across both sources.
    """
    fractions = {name: (1.0 if fired else 0.0) for name, fired in apollo_signals.items()}

    counts = defaultdict(int)
    n_runs = len(research_runs) or 1
    for run in research_runs:
        fired_this_run = {
            entry["signal_name"] for entry in run.get("signals_evaluated", []) if entry["fired"]
        }
        for name in fired_this_run:
            counts[name] += 1

    # Every signal referenced in any research run gets a fraction, even if it never fired.
    all_research_signal_names = {
        entry["signal_name"] for run in research_runs for entry in run.get("signals_evaluated", [])
    }
    for name in all_research_signal_names:
        fractions[name] = counts[name] / n_runs

    return fractions


def compute_capability_scores(fired_fractions: dict, signal_mapping: list, capabilities: list) -> dict:
    """
    Returns {capability_code: {"raw_score", "max_possible", "capability_score_unadjusted",
    "signals": [...]}} for every capability, per the formula in PRD SS6.3.
    """
    by_capability = defaultdict(list)
    for row in signal_mapping:
        by_capability[row["capability_code"]].append(row)

    results = {}
    for cap in capabilities:
        code = cap["short_code"]
        rows = by_capability.get(code, [])
        raw_score = 0.0
        max_possible = 0.0
        signal_details = []

        for row in rows:
            weight = row["weight"]
            fraction = fired_fractions.get(row["signal_name"], 0.0)
            raw_score += weight * fraction
            max_possible += weight

            detail = {
                "signal_name": row["signal_name"],
                "weight": weight,
                "hpr_element": row["hpr_element"],
                "fired_fraction": round(fraction, 4),
                "data_source": row["data_source"],
            }
            if fraction not in (0.0, 1.0):
                n_fired = round(fraction * 3)  # assumes 3 runs; informational only
                detail["variance_note"] = f"fired in {n_fired} of 3 research passes -- moderate confidence"
            signal_details.append(detail)

        unadjusted = (raw_score / max_possible * 100) if max_possible else 0.0
        results[code] = {
            "raw_score": round(raw_score, 4),
            "max_possible": max_possible,
            "capability_score_unadjusted": round(unadjusted, 2),
            "signals": signal_details,
        }
    return results


def apply_plausibility_gate(company_industry: str, company_stage: str, org_profile: dict, unadjusted_score: float) -> dict:
    """
    Deterministic lookup against org_profile's fixed lists (PRD SS6.4). No model involved.
    Returns {"discount_applied": bool, "reason": str|None, "final_score": float}.
    """
    industry_ok = company_industry in org_profile.get("industries_served", [])
    stage_ok = company_stage in org_profile.get("company_stages_served", [])

    if industry_ok and stage_ok:
        return {"discount_applied": False, "reason": None, "final_score": round(unadjusted_score, 2)}

    mismatches = []
    if not industry_ok:
        mismatches.append(f"industry '{company_industry}' not in Altus's served-industries list")
    if not stage_ok:
        mismatches.append(f"stage '{company_stage}' not in Altus's served-stages list")

    discounted = unadjusted_score * PLAUSIBILITY_DISCOUNT
    reason = (
        f"{'; '.join(mismatches)}. Score reduced from {round(unadjusted_score, 1)} "
        f"to {round(discounted, 1)} ({PLAUSIBILITY_DISCOUNT}x discount applied)."
    )
    return {"discount_applied": True, "reason": reason, "final_score": round(discounted, 2)}


def assign_band(final_score: float, signals: list) -> str:
    """
    Bands per PRD SS6.5. 'Signals fired' below is computed at fired_fraction >= 0.5
    (i.e. fired more often than not across runs) purely for the *count* used in the band
    criteria text -- it does not affect the score itself, which already uses the full
    fractional value.
    """
    fired_count = sum(1 for s in signals if s["fired_fraction"] >= 0.5)
    if fired_count < 2:
        return "Insufficient Evidence"
    for band_name, threshold in BAND_THRESHOLDS:
        if final_score >= threshold:
            return band_name
    return "Insufficient Evidence"


def pick_reference(capability_code: str, company_industry: str, company_stage: str, references: list):
    """Citation rule (PRD SS6.6): only cite if industry OR stage tag matches."""
    candidates = [r for r in references if r["capability_code"] == capability_code and r.get("used_in_rationale")]
    for ref in candidates:
        if ref.get("industry_tag") == company_industry or ref.get("stage_tag") == company_stage:
            return {"client_name": ref["client_name"], "outcome_metric": ref["outcome_metric"]}
    return None


def score_prospect(
    company_name: str,
    company_industry: str,
    company_stage: str,
    apollo_signals: dict,
    research_runs: list,
    capabilities: list,
    signal_mapping: list,
    references: list,
    org_profile: dict,
) -> dict:
    """End-to-end deterministic scoring for one company, given already-gathered signal evidence."""
    fired_fractions = aggregate_fired_fractions(apollo_signals, research_runs)
    raw_results = compute_capability_scores(fired_fractions, signal_mapping, capabilities)

    capability_records = []
    scores_for_tiebreak = {}

    for cap in capabilities:
        code = cap["short_code"]
        r = raw_results[code]
        gate = apply_plausibility_gate(company_industry, company_stage, org_profile, r["capability_score_unadjusted"])
        band = assign_band(gate["final_score"], r["signals"])
        cited_ref = pick_reference(code, company_industry, company_stage, references) if band != "Insufficient Evidence" else None

        fired_signal_names = [s["signal_name"] for s in r["signals"] if s["fired_fraction"] >= 0.5]
        if band == "Insufficient Evidence":
            rationale = f"Insufficient signal for {cap['name']}: fewer than 2 signals fired with confidence."
        else:
            rationale = (
                f"{cap['name']} scored {gate['final_score']} ({band}). "
                f"Signals fired: {', '.join(fired_signal_names) if fired_signal_names else 'none above threshold'}."
            )
            if gate["discount_applied"]:
                rationale += f" NOTE: {gate['reason']}"

        scores_for_tiebreak[code] = gate["final_score"]

        capability_records.append({
            "capability_code": code,
            "capability_name": cap["name"],
            "signals": r["signals"],
            "raw_score": r["raw_score"],
            "max_possible": r["max_possible"],
            "capability_score_unadjusted": r["capability_score_unadjusted"],
            "plausibility_discount_applied": gate["discount_applied"],
            "plausibility_discount_reason": gate["reason"],
            "capability_score_final": gate["final_score"],
            "band": band,
            "rationale": rationale,
            "cited_reference": cited_ref,
        })

    capability_records.sort(key=lambda c: c["capability_score_final"], reverse=True)

    co_primary_tie = (
        len(capability_records) >= 2
        and abs(capability_records[0]["capability_score_final"] - capability_records[1]["capability_score_final"]) <= 5
        and capability_records[0]["band"] != "Insufficient Evidence"
    )

    sources_consulted = sorted({src for run in research_runs for src in run.get("sources_consulted", [])})

    return {
        "company_name": company_name,
        "org_id": org_profile.get("org_id"),
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "capabilities": capability_records,
        "co_primary_tie": co_primary_tie,
        "sources_consulted": sources_consulted,
    }
