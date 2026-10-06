"""Command-line entry point for SDR account discovery."""
import argparse
import sys
import sqlite3
from dataclasses import replace

from config import get_settings
from leadiq import LeadIQError, test_auth
from providers import ProviderError
from prospect_discovery import ProspectFilters
from personas import PERSONAS, SKILL_GROUPS
from service import create_service
from database import database_path, reset_local_database


def main(argv=None):
    parser = argparse.ArgumentParser(description="SDR account discovery and local scrub data tools.")
    commands = parser.add_subparsers(dest="command", required=True)
    reset = commands.add_parser("reset-local-data", help="Delete only the local scrub database (stop Streamlit first)")
    reset.add_argument("--yes", action="store_true", help="Confirm deletion without prompting")
    auth = commands.add_parser("test-auth", help="Test LeadIQ credentials explicitly")
    auth.add_argument("--provider", choices=["mock", "leadiq"])
    search = commands.add_parser("search", help="Match, score and rank candidate accounts")
    search.add_argument("--provider", choices=["mock", "leadiq"])
    search.add_argument("--salesforce-provider", choices=["mock", "csv", "disabled"])
    search.add_argument("--salesforce-csv-path")
    search.add_argument("--country")
    search.add_argument("--min-employees", type=int)
    search.add_argument("--max-employees", type=int)
    search.add_argument("--industry")
    search.add_argument("--min-score", type=int, default=0)
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--show-all", action="store_true")
    people = commands.add_parser("prospects", help="Find and rank people inside one company")
    people.add_argument("--provider", choices=["mock", "leadiq"])
    people.add_argument("--company-id", required=True)
    people.add_argument("--persona", choices=list(PERSONAS))
    people.add_argument("--job-title", action="append", default=[])
    people.add_argument("--seniority", action="append", default=[])
    people.add_argument("--function", action="append", default=[])
    people.add_argument("--skill-group", choices=list(SKILL_GROUPS), action="append", default=[])
    people.add_argument("--min-prospect-score", type=int, default=0)
    people.add_argument("--prospects-per-company", "--limit", dest="prospects_per_company", type=int, default=3)
    args = parser.parse_args(argv)
    try:
        if args.command == "reset-local-data":
            path = database_path()
            print(f"Local scrub database: {path}. Stop Streamlit before resetting.")
            if not args.yes:
                try:
                    confirmed = input("Type RESET to delete all local daily lists: ").strip() == "RESET"
                except (EOFError, KeyboardInterrupt):
                    confirmed = False
                if not confirmed:
                    print("Cancelled. Local data was preserved.")
                    return 0
            removed = reset_local_database(path)
            print("Local database deleted. The app will initialize a new one on startup." if removed else "No local database exists.")
            return 0
        settings = get_settings()
        if args.provider:
            changes = {"prospect" if args.command == "prospects" else "account": args.provider}
            # Explicit live CLI searches default to unchecked CRM unless separately configured.
            if args.command == "search" and args.provider == "leadiq" and settings.salesforce == "mock":
                changes["salesforce"] = "disabled"
            settings = replace(settings, **changes)
        if args.command == "search":
            if args.salesforce_provider:
                settings = replace(settings, salesforce=args.salesforce_provider)
            if args.salesforce_csv_path is not None:
                settings = replace(settings, salesforce_csv_path=args.salesforce_csv_path)
        mock = settings.prospect == "mock" if args.command == "prospects" else settings.account == "mock"
        if args.command == "test-auth":
            mock = settings.account == "mock" and settings.prospect == "mock"
            if mock:
                print("Mock mode: authentication test skipped. No API requests were made.\n"
                      "Run test-auth --provider leadiq to test real credentials.")
            else:
                test_auth(allow_live=True)
                print("LeadIQ authentication successful: account query completed.")
            return 0
        if args.command == "prospects":
            filters = ProspectFilters(persona=args.persona, job_titles=tuple(args.job_title),
                                      seniorities=tuple(args.seniority), functions=tuple(args.function),
                                      skill_groups=tuple(args.skill_group), min_score=args.min_prospect_score,
                                      limit=args.prospects_per_company)
            service = create_service(settings)
            ranked = service.find_better_prospects(args.company_id, filters)
            print("Demo mode — fictional mock prospect data" if mock else "Live prospect data")
            if not ranked["results"]:
                print("No prospects match this company and filters. The account remains eligible.")
            for i, prospect in enumerate(ranked["results"], 1):
                score = prospect["prospect_score"]
                print(f"#{i} {prospect['full_name']} — {prospect['job_title']}\n"
                      f"Prospect Score: {score['total_score']} / 100 — {score['score_band']}\n"
                      f"Relevant skills: {', '.join(score['matched_skills']) or 'Unknown'}\n")
            return 0
        service = create_service(settings)
        result = service.search_accounts(country=args.country, industry=args.industry,
                                         min_employees=args.min_employees, max_employees=args.max_employees,
                                         min_score=args.min_score, limit=args.limit, show_all=args.show_all)
        print("MOCK DATA — fictional candidate companies." if mock else "Live LeadIQ candidate data")
        print(f"Account source: {settings.account}; Salesforce source: {settings.salesforce}")
        for warning in result["warnings"]:
            print("Warning: " + warning)
        summary = result["summary"]
        print(f"Total candidates: {summary['total_candidates']}\n"
              f"Already in Salesforce: {summary['EXISTS']}\n"
              f"Possible matches: {summary['POSSIBLE_MATCH']}\n"
              f"Net-new accounts: {summary['NET_NEW']}\n"
              f"Not checked: {summary['NOT_CHECKED']}\n"
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
    except (LeadIQError, ProviderError, ValueError, sqlite3.Error, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
