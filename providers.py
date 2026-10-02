"""Adapters return canonical records; matching/scoring never read files or APIs."""
import csv
import json

from config import PROJECT_DIR, use_mock_data
from leadiq import search_companies


class ProviderError(Exception):
    pass


class MockLeadIQProvider:
    def get_companies(self):
        try:
            with (PROJECT_DIR / "mock_leadiq_data.json").open() as source:
                return json.load(source)
        except (OSError, ValueError) as exc:
            raise ProviderError("Cannot read mock_leadiq_data.json. Check the local fixture file.") from exc


class LeadIQAPIProvider:
    def get_companies(self):
        # One bounded API page. Never manufacture the four unavailable ICP signals.
        return [{"company_id": row["id"], "company_name": row["name"],
                 **{key: value for key, value in row.items() if key not in {"id", "name"}},
                 "it_headcount": None, "number_of_locations": None,
                 "it_hiring": None, "growth_signal": None}
                for row in search_companies(limit=100)]


class MockSalesforceProvider:
    def get_accounts(self):
        try:
            with (PROJECT_DIR / "mock_salesforce_accounts.csv").open(newline="") as source:
                return list(csv.DictReader(source))
        except OSError as exc:
            raise ProviderError("Cannot read mock_salesforce_accounts.csv. Check the local fixture file.") from exc


class SalesforceAPIProvider:
    def get_accounts(self):
        raise ProviderError("Live Salesforce integration is not implemented. Use USE_MOCK_DATA=true "
                            "for the discovery pipeline. The existing LeadIQ test-auth works in live mode.")


def get_providers():
    if use_mock_data():
        return MockLeadIQProvider(), MockSalesforceProvider()
    return LeadIQAPIProvider(), SalesforceAPIProvider()
