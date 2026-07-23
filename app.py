"""
Altus Fit-Scoring prototype app.

Run with:
    streamlit run app.py

This is a Phase 1 prototype UI, not a production app: single-process, no auth, no
persistence between sessions beyond what's in the browser tab. Its job is to let you
(a) supply API keys at runtime instead of env vars, (b) score one company at a time, and
(c) upload the Validation Set sheet and see how scoring output compares to partner
judgment (PRD SS8) -- the layer that was deliberately deferred until the engine was built.
"""
import io
import json
import tempfile

import pandas as pd
import streamlit as st

from src.pipeline import score_company
from src.validation import load_validation_rows, evaluate_row, summarize

st.set_page_config(page_title="Altus Fit Scoring — Prototype", layout="wide")

# ---------------------------------------------------------------------------
# Sidebar: API keys + mode
# ---------------------------------------------------------------------------
st.sidebar.title("Configuration")

mode = st.sidebar.radio(
    "Mode",
    ["Mock (no keys needed)", "Live (requires Apollo + Anthropic keys)"],
    help="Mock mode uses synthetic data to exercise the scoring engine without any live calls.",
)

apollo_key = None
anthropic_key = None
mock_scenario = None

if mode.startswith("Live"):
    apollo_key = st.sidebar.text_input("Apollo API Key", type="password", help="Altus's master Apollo API key")
    anthropic_key = st.sidebar.text_input("Anthropic API Key", type="password")
    if not apollo_key or not anthropic_key:
        st.sidebar.warning("Both keys are required for a live run.")
else:
    mock_scenario = st.sidebar.selectbox(
        "Mock scenario",
        ["spo_strong", "mismatch_stage"],
        help="spo_strong: strong SPO-fit company. mismatch_stage: exercises the plausibility discount.",
    )

st.sidebar.divider()
st.sidebar.caption(
    "Keys entered here are used only for this session's requests — they are not saved to "
    "disk by this app. For a real deployment, keys should be handled by your hosting "
    "platform's secrets manager, not typed into a browser field each time."
)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
st.title("Altus Alliance — Prospect Fit Scoring")
st.caption("Prototype UI over the scoring pipeline defined in the Phase 1 PRD.")

tab_score, tab_validate = st.tabs(["Score a Prospect", "Validation Set"])

# --- Tab 1: score a single company ---
with tab_score:
    st.subheader("Score one company")

    col1, col2 = st.columns(2)
    with col1:
        company_name = st.text_input("Company name", value="Acme Robotics" if mock_scenario else "")
    with col2:
        domain = st.text_input("Domain", value="acmerobotics.com" if mock_scenario else "", disabled=bool(mock_scenario))

    run_disabled = mode.startswith("Live") and (not apollo_key or not anthropic_key)

    if st.button("Score company", type="primary", disabled=run_disabled or not company_name):
        with st.spinner("Scoring... (live mode runs 3 research passes and can take a minute)"):
            try:
                result = score_company(
                    company_name=company_name,
                    domain=domain or None,
                    mock_scenario=mock_scenario,
                    apollo_api_key=apollo_key,
                    anthropic_api_key=anthropic_key,
                )
                st.session_state["last_result"] = result
            except Exception as e:
                st.error(f"Scoring failed: {e}")

    if "last_result" in st.session_state:
        result = st.session_state["last_result"]
        st.success(f"Scored: {result['company_name']}")

        rows = []
        for cap in result["capabilities"]:
            rows.append({
                "Capability": cap["capability_name"],
                "Score": cap["capability_score_final"],
                "Band": cap["band"],
                "Discount applied": cap["plausibility_discount_applied"],
                "Rationale": cap["rationale"],
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        if result.get("co_primary_tie"):
            st.info("Top two capabilities are within 5 points — treat as co-primary rather than a single winner.")

        with st.expander("Full JSON output"):
            st.json(result)

        st.download_button(
            "Download result as JSON",
            data=json.dumps(result, indent=2, default=str),
            file_name=f"{result['company_name'].replace(' ', '_')}_scoring.json",
            mime="application/json",
        )

# --- Tab 2: validation set ---
with tab_validate:
    st.subheader("Run the Validation Set")
    st.write(
        "Upload the filled-out `Altus_Validation_Set_Template.xlsx` to score every real "
        "company in it and compare the pipeline's output against partner judgment."
    )

    uploaded = st.file_uploader("Validation Set (.xlsx)", type=["xlsx"])

    run_val_disabled = mode.startswith("Live") and (not apollo_key or not anthropic_key)

    if uploaded is not None:
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp.write(uploaded.getvalue())
            tmp_path = tmp.name

        try:
            validation_rows = load_validation_rows(tmp_path)
        except Exception as e:
            st.error(f"Could not read the sheet: {e}")
            validation_rows = []

        if validation_rows:
            st.write(f"Found **{len(validation_rows)}** company row(s) to validate.")
            if mode.startswith("Live"):
                st.warning(
                    f"Live mode will make {len(validation_rows)} Apollo calls and "
                    f"{len(validation_rows) * 3} model research calls. This can take a while and "
                    "will cost real API usage."
                )

            if st.button("Run validation", type="primary", disabled=run_val_disabled):
                evaluations = []
                progress = st.progress(0.0)
                for i, row in enumerate(validation_rows):
                    company = row.get("Company Name")
                    domain_val = row.get("Domain / Website")
                    try:
                        result = score_company(
                            company_name=company,
                            domain=domain_val,
                            mock_scenario=mock_scenario,
                            apollo_api_key=apollo_key,
                            anthropic_api_key=anthropic_key,
                        )
                        evaluations.append(evaluate_row(row, result))
                    except Exception as e:
                        evaluations.append({
                            "company_name": company,
                            "error": str(e),
                        })
                    progress.progress((i + 1) / len(validation_rows))

                st.session_state["validation_evaluations"] = evaluations

    if "validation_evaluations" in st.session_state:
        evaluations = st.session_state["validation_evaluations"]
        clean_evals = [e for e in evaluations if "error" not in e]
        errored = [e for e in evaluations if "error" in e]

        summary = summarize(clean_evals)
        c1, c2, c3 = st.columns(3)
        c1.metric("Companies scored", summary["n"])
        c2.metric("Top-1 capability match", f"{summary['top1_accuracy']*100:.0f}%" if summary["top1_accuracy"] is not None else "—")
        c3.metric("Band match (partner's capability)", f"{summary['band_accuracy']*100:.0f}%" if summary["band_accuracy"] is not None else "—")

        st.dataframe(pd.DataFrame(clean_evals), use_container_width=True, hide_index=True)

        if errored:
            st.error(f"{len(errored)} row(s) failed to score:")
            st.dataframe(pd.DataFrame(errored), use_container_width=True, hide_index=True)

        st.download_button(
            "Download validation results as CSV",
            data=pd.DataFrame(clean_evals).to_csv(index=False),
            file_name="validation_results.csv",
            mime="text/csv",
        )

        st.caption(
            "Top-1 match: did the pipeline rank the partner's chosen capability #1? "
            "Band match: for that specific capability, did the predicted band match the "
            "partner's Fit Judgment? Low accuracy here means revisiting the weight matrix "
            "(signal_mapping.json), not the app."
        )
