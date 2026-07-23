#!/usr/bin/env python3
"""
Altus fit-scoring prototype -- CLI entry point.

Live run (requires APOLLO_API_KEY and ANTHROPIC_API_KEY env vars, and network access):
    python main.py --company "Acme Robotics" --domain acmerobotics.com

Mock run (no keys/network needed -- proves the scoring engine end to end):
    python main.py --company "Acme Robotics" --mock spo_strong
    python main.py --company "Globex Industrial" --mock mismatch_stage
"""
import argparse
import json
import sys

from src.pipeline import score_company


def main():
    parser = argparse.ArgumentParser(description="Score a prospect against Altus capabilities.")
    parser.add_argument("--company", required=True, help="Company name")
    parser.add_argument("--domain", help="Company domain (required for live Apollo lookup)")
    parser.add_argument("--mock", choices=["spo_strong", "mismatch_stage"], help="Use synthetic data instead of live Apollo/Claude calls")
    parser.add_argument("--out", help="Path to write JSON output (default: stdout only)")
    args = parser.parse_args()

    if not args.mock and not args.domain:
        print("Error: --domain is required for a live run (or use --mock for a local test).", file=sys.stderr)
        sys.exit(1)

    result = score_company(company_name=args.company, domain=args.domain, mock_scenario=args.mock)

    output_json = json.dumps(result, indent=2, default=str)
    print(output_json)

    if args.out:
        with open(args.out, "w") as f:
            f.write(output_json)
        print(f"\nWritten to {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
