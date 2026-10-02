"""Command-line entry point for SDR account discovery."""
import argparse
import sys

from config import use_mock_data
from discovery import discover_accounts
from leadiq import LeadIQError, test_auth
from providers import ProviderError, get_providers


def main(argv=None):
    parser = argparse.ArgumentParser(description="Discover and rank NET_NEW SDR accounts.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("test-auth", help="Test credentials in live mode; no network in mock mode")
    search = commands.add_parser("search", help="Match, score and rank candidate accounts")
    search.add_argument("--country")
    search.add_argument("--min-employees", type=int)
    search.add_argument("--max-employees", type=int)
    search.add_argument("--industry")
    search.add_argument("--min-score", type=int, default=0)
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--show-all", action="store_true")
    args = parser.parse_args(argv)
    try:
        mock = use_mock_data()
        if args.command == "test-auth":
            if mock:
                print("Mock mode: authentication test skipped. No API requests were made.\n"
                      "Set USE_MOCK_DATA=false to test real LeadIQ credentials.")
            else:
                test_auth()
                print("LeadIQ authentication successful: account query completed.")
            return 0
        leadiq, salesforce = get_providers()
        # Check Salesforce availability before spending any LeadIQ credits.
        accounts = salesforce.get_accounts()
        result = discover_accounts(leadiq.get_companies(), accounts, country=args.country,
                                   industry=args.industry, min_employees=args.min_employees,
                                   max_employees=args.max_employees, min_score=args.min_score,
                                   limit=args.limit, show_all=args.show_all)
        print("MOCK DATA — fictional companies, not real LeadIQ or Salesforce results." if mock
              else "Live candidate data")
        summary = result["summary"]
        print(f"Total candidates: {summary['total_candidates']}\n"
              f"Already in Salesforce: {summary['EXISTS']}\n"
              f"Possible matches: {summary['POSSIBLE_MATCH']}\n"
              f"Net-new accounts: {summary['NET_NEW']}\n"
              f"Showing {len(result['results'])} of {result['eligible_results']} eligible accounts\n")
        if not result["results"]:
            print("No accounts match the selected status and score filters.")
        for company in result["results"]:
            for label, field in (("Company", "company_name"), ("Domain", "domain"),
                                 ("Employees", "employee_count"), ("Industry", "industry"),
                                 ("Country", "country"), ("City", "city"),
                                 ("Salesforce Status", "salesforce_status")):
                value = company.get(field)
                print(f"{label}: {value if value is not None else 'Not available'}")
            print(f"ICP Score: {company['score']['total_score']}\n"
                  f"Matching Contacts: {company.get('matching_contacts', 'Not available')}\n\nReasons:")
            for reason in company["score"]["reasons"]:
                print(f"- {reason}")
            if not company["score"]["reasons"]:
                print("- No scoring signals available")
            print("\n-----------------------------------")
    except (LeadIQError, ProviderError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
