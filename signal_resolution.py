"""
Resolves the Apollo-tagged signals in signal_mapping.json deterministically from an
Apollo organization record. No model call involved -- same input always gives same output.

This is intentionally separate from the model-driven research step (research_agent.py):
per the PRD, only "Research" and "Apollo + Research" signals need the model at all, and
even those only need the model for the *research/context* part -- the base "did this
happen" fact for "Apollo + Research" rows still comes from here first.
"""
from datetime import datetime, timezone

CRM_KEYWORDS = ["salesforce", "hubspot crm", "pipedrive", "zoho crm", "dynamics 365", "sfa"]


def _has_crm(technologies: list) -> bool:
    techs = [t.lower() for t in technologies]
    return any(any(kw in t for kw in CRM_KEYWORDS) for t in techs)


def _role_vacant_signal(job_postings: list, roles=("vp sales", "vp business development", "vp marketing")) -> bool:
    for posting in job_postings:
        title = posting.get("title", "").lower()
        if any(r in title for r in roles) and posting.get("days_open", 0) > 60:
            return True
    return False


def _headcount_growth_signal(job_postings: list, threshold: int = 3) -> bool:
    """Proxy: 3+ concurrent open S&M postings outpacing typical single-role backfill."""
    sm_roles = ("account executive", "sdr", "bdr", "sales", "marketing")
    sm_postings = [p for p in job_postings if any(r in p.get("title", "").lower() for r in sm_roles)]
    return len(sm_postings) >= threshold


def _recent_leadership_change(leadership_changes: list, months: int = 6) -> bool:
    now = datetime.now(timezone.utc)
    for change in leadership_changes:
        role = change.get("role", "").lower()
        if role not in ("ceo", "cro", "cmo"):
            continue
        try:
            change_date = datetime.fromisoformat(change["date"]).replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        if (now - change_date).days <= months * 30:
            return True
    return False


def resolve_apollo_signals(apollo_org: dict) -> dict:
    """
    Returns {signal_name: bool} for every signal tagged "Apollo" or "Apollo + Research"
    in signal_mapping.json. The "Apollo + Research" entries get their base fact resolved
    here; the model's research pass (research_agent.py) adds context/evidence on top but
    does not override this boolean.
    """
    funding_stage = (apollo_org.get("funding_stage") or "").lower()
    early_stage_rounds = {"seed", "pre-seed", "series a", "series b"}

    return {
        "Recent funding round (Seed-Series B)": funding_stage in early_stage_rounds,
        "PE/VC add-on or portfolio-company context": bool(apollo_org.get("is_pe_backed")),
        "M&A activity (acquired or acquiring)": bool(apollo_org.get("ma_events")),
        "New CEO/CRO/CMO within ~6 months": _recent_leadership_change(apollo_org.get("leadership_changes", [])),
        "VP Sales/BD/Marketing role open >60 days or recently vacated": _role_vacant_signal(
            apollo_org.get("open_job_postings", [])
        ),
        "Rapid S&M headcount growth (postings outpacing backfill)": _headcount_growth_signal(
            apollo_org.get("open_job_postings", [])
        ),
        "New product launch / market expansion announcement": bool(apollo_org.get("partnership_announcements"))
        or bool(apollo_org.get("_product_launch_flag")),  # placeholder: Apollo has no dedicated field for this
        "Outdated or missing CRM/SFA in tech stack": not _has_crm(apollo_org.get("technologies", [])),
        "New partnership/channel announcement": bool(apollo_org.get("partnership_announcements")),
    }
