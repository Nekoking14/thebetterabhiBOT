"""Read-only discovery → account/prospect scores → durable daily scrub list."""
from dataclasses import replace

from config import get_settings
from discovery import discover_accounts
from leadiq import LeadIQError
from prospect_discovery import ProspectFilters
from providers import ProviderError
from service import create_service


class ScrubService:
    def __init__(self, discovery, repository):
        self.discovery = discovery
        self.repository = repository

    def find_accounts(self, *, prospect_filters=None, day=None, **account_filters):
        prospect_filters = prospect_filters or ProspectFilters()
        # Validate before provider calls. No CRM provider is read in this journey.
        discover_accounts([], None, **account_filters)
        source_filters = {key: account_filters.get(key) for key in
                          ("country", "industry", "min_employees", "max_employees")}
        companies = self.discovery.account_provider.get_companies(**source_filters)
        result = discover_accounts(companies, None, **account_filters)
        session = self.repository.ensure_session(day)
        warnings = []
        people_failed = False
        for account in result["results"]:
            account.pop("salesforce_status", None)
            account["prospect_source"] = self.discovery.prospect_provider.source
            # Avoid paid people searches for accounts already on today's list.
            if self.repository.contains(session["session_id"], account):
                continue
            if people_failed:
                continue
            try:
                if account["source"] == "mock" and account["prospect_source"] == "leadiq":
                    raise ProviderError("Mock company IDs cannot be searched in live LeadIQ. Select mock prospects or live accounts.")
                account["ranked_prospects"] = self.discovery.find_better_prospects(account["company_id"], prospect_filters)
            except (LeadIQError, ProviderError, ValueError) as exc:
                # Keep strong companies even if people are unavailable. Stop the
                # batch after a provider error rather than repeatedly failing calls.
                warnings.append(f"Prospect search stopped: {exc}. Accounts were retained; use Find Better Prospects later.")
                people_failed = True
        counts = self.repository.append(session["session_id"], result["results"])
        candidates = result["summary"]["total_candidates"]
        return {**counts, "candidates": candidates,
                "below_threshold": candidates - result["eligible_results"],
                "limited": result["eligible_results"] - len(result["results"]), "warnings": warnings}

    def find_better(self, account, filters):
        provider_source = self.discovery.prospect_provider.source
        if account["source"] != provider_source:
            raise ProviderError(f"Select the {account['source']} prospect source to search this stored account.")
        if not account["company_id"]:
            raise ProviderError("This account has no provider company ID. Discover it again with a company ID before searching people.")
        ranked = self.discovery.find_better_prospects(account["company_id"], filters)
        self.repository.update_prospects(account["id"], ranked, provider_source)
        return ranked


def create_scrub_service(repository, settings=None):
    settings = settings or get_settings(include_salesforce=False)
    return ScrubService(create_service(replace(settings, salesforce="disabled")), repository)
