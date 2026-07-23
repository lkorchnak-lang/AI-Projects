"""
Model-driven research pass. Calls the Claude API with the system prompt from PRD SS6.7
and the web_search tool, asking the model to judge only the "Research" and
"Apollo + Research" tagged signals for one company. Called 3x per company (PRD SS6.3a);
aggregation happens in scoring_engine.py, not here.

This has not been live-tested in this sandbox (no ANTHROPIC_API_KEY available here, and
api.anthropic.com calls from a throwaway sandbox key aren't a meaningful test of research
quality anyway). Wire in a real key via ANTHROPIC_API_KEY before running for real.
"""
import os
import json

SYSTEM_PROMPT = """You are a research and signal-detection assistant supporting Altus Alliance's prospect
fit-scoring pipeline. You are NOT responsible for computing a fit score, a band, or any
arithmetic - a separate deterministic process handles that from your output. Your job is
strictly:

1. You will be given: (a) a target company's name, domain, and Apollo-sourced firmographic
   fields, and (b) a list of signals tagged "Research" or "Apollo + Research" that require
   you to investigate the company's public presence.

2. For each "Research" or "Apollo + Research" signal, investigate the company's homepage,
   product pages, case studies/customer quotes, job descriptions, partner listings, public
   sales decks or webinar topics, social media (X, Facebook, Instagram, LinkedIn), and recent
   press releases as available. Do not rely on assumptions - base every judgment on something
   you actually found.

3. For each signal, determine whether it fired (true/false) and give one to two sentences of
   evidence in your own words. Never quote source text directly or at length - paraphrase.

4. Do not evaluate "Apollo"-only signals - those are provided to you as already-resolved
   facts and should be passed through unchanged in your output.

5. Do not compute a score, percentage, or ranking of any kind. Do not apply the plausibility
   gate. Do not decide which capability the company is best suited for. That is out of scope
   for this call.

6. Output ONLY valid JSON matching the schema you are given. No preamble, no markdown code
   fences, no commentary outside the JSON object."""

OUTPUT_SCHEMA_HINT = """Return JSON matching exactly:
{
  "company_name": "string",
  "run_number": <int>,
  "signals_evaluated": [
    {
      "signal_name": "string (must match the signal name given to you exactly)",
      "capability_code": "ADD | MDB | SPO | EL",
      "fired": true/false,
      "evidence": "string, paraphrased, 1-2 sentences",
      "source_checked": "string, e.g. 'homepage', 'job postings page'"
    }
  ],
  "sources_consulted": ["string, URLs or pages actually reviewed"]
}"""


def build_user_message(company_name: str, domain: str, apollo_context: dict, signals_to_research: list, run_number: int) -> str:
    signal_list_str = "\n".join(
        f"- {s['signal_name']} (capability: {s['capability_code']}, data_source: {s['data_source']})"
        for s in signals_to_research
    )
    return f"""Company: {company_name}
Domain: {domain}
Run number: {run_number}
Known Apollo context: {json.dumps(apollo_context, default=str)}

Signals to research and judge:
{signal_list_str}

{OUTPUT_SCHEMA_HINT}"""


def run_research_pass(company_name: str, domain: str, apollo_context: dict, signals_to_research: list, run_number: int, client=None, anthropic_api_key: str = None) -> dict:
    """
    Executes one research pass. `client` is an anthropic.Anthropic() instance; if None,
    constructs one using anthropic_api_key (falls back to ANTHROPIC_API_KEY env var).
    Uses the web_search tool so the model can actually browse rather than guessing from
    training data.
    """
    if not signals_to_research:
        return {"company_name": company_name, "run_number": run_number, "signals_evaluated": [], "sources_consulted": []}

    if client is None:
        import anthropic  # local import: only required when actually calling the live API
        client = anthropic.Anthropic(api_key=anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY"))

    user_message = build_user_message(company_name, domain, apollo_context, signals_to_research, run_number)

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": user_message}],
    )

    text_blocks = [b.text for b in response.content if getattr(b, "type", None) == "text"]
    joined = "\n".join(text_blocks).strip()
    # Defensive parse: strip accidental code fences if the model adds them anyway.
    if joined.startswith("```"):
        joined = joined.strip("`")
        if joined.lower().startswith("json"):
            joined = joined[4:]
    return json.loads(joined)


def get_mock_research_pass(company_name: str, run_number: int, signals_to_research: list) -> dict:
    """
    Synthetic research-pass output for local testing without a live API key. Deliberately
    varies one signal across runs 1-3 to exercise the fired_fraction / variance_note logic
    in scoring_engine.py.
    """
    fixed_results = {
        "Weak/unfocused site messaging, no clear problem-solution statement": True,
        "No clear CTA / conversion path on site": True,
        "No clear industry/sector focus (too broad)": False,
        "Missing or thin case studies/references on site": True,
        "Visibly AI-generated copy/images/logo": False,
        "Poor SEO/GEO indexability, no ad-spend signal": True,
        "Conference sponsorship/speaking activity": False,
        "M&A activity (acquired or acquiring)": False,
        "New product launch / market expansion announcement": True,
        "New partnership/channel announcement": False,
    }
    # This one flips on run 2 only, to simulate genuine research ambiguity.
    variable_signal = "New product launch / market expansion announcement"
    fixed_results[variable_signal] = run_number in (1, 2)

    evaluated = []
    for s in signals_to_research:
        name = s["signal_name"]
        fired = fixed_results.get(name, False)
        evaluated.append({
            "signal_name": name,
            "capability_code": s["capability_code"],
            "fired": fired,
            "evidence": f"[mock run {run_number}] {'Found supporting evidence on site/job postings.' if fired else 'No supporting evidence found.'}",
            "source_checked": "homepage" if "site" in name.lower() or "cta" in name.lower() else "press/news",
        })

    return {
        "company_name": company_name,
        "run_number": run_number,
        "signals_evaluated": evaluated,
        "sources_consulted": [f"https://{company_name.lower().replace(' ', '')}.com (mock)"],
    }
