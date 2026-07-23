"""
Apollo.io integration.

NOTE: This sandbox's network allowlist does not include api.apollo.io, so this client
has NOT been live-tested against the real Apollo API. It's written against Apollo's
documented v1 organization-enrichment endpoint. Before relying on it:
  1. Confirm exact field names against Apollo's current API reference (PRD SS10, open Q5).
  2. Test against a real org_id/domain with a live key.

Set APOLLO_API_KEY as an environment variable (Altus's master key).
"""
import os
import requests

APOLLO_BASE_URL = "https://api.apollo.io/v1"


class ApolloClient:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.environ.get("APOLLO_API_KEY")
        if not self.api_key:
            raise ValueError(
                "No Apollo API key found. Set APOLLO_API_KEY env var or pass api_key= explicitly."
            )

    def get_organization(self, domain: str = None, org_id: str = None) -> dict:
        """
        Fetch firmographic data for one company.

        Returns a dict with (at minimum) the fields the signal resolver expects:
        funding_stage, funding_events, is_pe_backed, ma_events, employee_count_history,
        leadership_changes, open_job_postings, technologies, industry, founded_year.

        This method assumes Apollo's /organizations/enrich and /mixed_people/search-style
        endpoints; the exact response shape should be verified and this parsing logic
        adjusted once tested live.
        """
        if not domain and not org_id:
            raise ValueError("Must provide domain or org_id")

        params = {"api_key": self.api_key}
        if domain:
            params["domain"] = domain
        if org_id:
            params["id"] = org_id

        resp = requests.get(f"{APOLLO_BASE_URL}/organizations/enrich", params=params, timeout=20)
        resp.raise_for_status()
        raw = resp.json()

        # Normalize into the shape signal_resolution.py expects.
        # Field paths below are best-effort based on Apollo's documented schema and MUST
        # be checked against a live response before this is trusted.
        org = raw.get("organization", {})
        return {
            "name": org.get("name"),
            "domain": org.get("primary_domain") or domain,
            "industry": org.get("industry"),
            "founded_year": org.get("founded_year"),
            "employee_count": org.get("estimated_num_employees"),
            "funding_stage": org.get("latest_funding_stage"),
            "funding_events": org.get("funding_events", []),
            "is_pe_backed": bool(org.get("owned_by_organization")),
            "technologies": org.get("technologies", []),
            "leadership_changes": org.get("recent_leadership_changes", []),  # verify field name
            "open_job_postings": org.get("job_postings", []),  # verify field name
            "ma_events": org.get("m_and_a_events", []),  # verify field name
            "partnership_announcements": org.get("partnership_announcements", []),  # verify field name
            "_raw": org,
        }


def get_mock_organization(scenario: str = "spo_strong") -> dict:
    """
    Synthetic Apollo response for local testing without network access or a live key.
    Used by main.py --mock and by the test harness.
    """
    scenarios = {
        "spo_strong": {
            "name": "Acme Robotics",
            "domain": "acmerobotics.com",
            "industry": "Tech & Software",
            "founded_year": 2019,
            "employee_count": 210,
            "funding_stage": "Series B",
            "funding_events": [{"date": "2026-02-01", "amount": "35M", "stage": "Series B"}],
            "is_pe_backed": False,
            "technologies": ["HubSpot Marketing", "Google Workspace"],  # no CRM/SFA present
            "leadership_changes": [],
            "open_job_postings": [
                {"title": "VP Sales", "days_open": 95},
                {"title": "Account Executive", "days_open": 20},
                {"title": "Account Executive", "days_open": 18},
                {"title": "SDR", "days_open": 15},
            ],
            "ma_events": [],
            "partnership_announcements": [],
        },
        "mismatch_stage": {
            "name": "Globex Industrial",
            "domain": "globexindustrial.com",
            "industry": "Manufacturing",  # not in Altus's served industries
            "founded_year": 1998,
            "employee_count": 4200,
            "funding_stage": "Public",  # not in served stages list as configured
            "funding_events": [],
            "is_pe_backed": False,
            "technologies": ["Salesforce", "SAP"],
            "leadership_changes": [{"role": "CRO", "date": "2026-04-01"}],
            "open_job_postings": [],
            "ma_events": [{"date": "2026-01-10", "type": "acquired_competitor"}],
            "partnership_announcements": [],
        },
    }
    if scenario not in scenarios:
        raise ValueError(f"Unknown mock scenario '{scenario}'. Options: {list(scenarios)}")
    return scenarios[scenario]
