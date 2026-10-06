"""Application service: independent data sources, staged requests and session cache."""
from config import get_settings
from discovery import discover_accounts
from leadiq import LeadIQClient
from prospect_discovery import find_better_prospects
from prospect_scoring import score_prospect
from providers import get_providers
from prospect_providers import get_prospect_provider


class DiscoveryService:
    def __init__(self, account_provider, salesforce_provider, prospect_provider, client=None):
        self.account_provider = account_provider
        self.salesforce_provider = salesforce_provider
        self.prospect_provider = prospect_provider
        self.client = client

    def search_accounts(self, **filters):
        # Validate before any paid/network calls.
        discover_accounts([], None, **filters)
        accounts = self.salesforce_provider.get_accounts()
        source_filters = {k: filters.get(k) for k in ('country', 'industry', 'min_employees', 'max_employees')}
        result = discover_accounts(self.account_provider.get_companies(**source_filters), accounts, **filters)
        result['warnings'] = [self.salesforce_provider.warning] if getattr(self.salesforce_provider, 'warning', None) else []
        return result

    def current_prospect(self, company_id):
        # Only local mock initial contacts auto-load. Live discovery is user-triggered.
        if getattr(self.prospect_provider, 'source', None) != 'mock':
            return None
        prospects = self.prospect_provider.get_prospects(company_id)
        return {**prospects[0], 'prospect_score': score_prospect(prospects[0])} if prospects else None

    def find_better_prospects(self, company_id, filters=None):
        return find_better_prospects(company_id, self.prospect_provider, filters)


def create_service(settings=None):
    settings = settings or get_settings()
    client = LeadIQClient(settings.cache_ttl_seconds)
    accounts, salesforce = get_providers(settings, client)
    return DiscoveryService(accounts, salesforce, get_prospect_provider(settings, client), client)
