"""Account and temporary CRM adapters over canonical records."""
import csv
import json
from pathlib import Path

from config import PROJECT_DIR, get_settings
from leadiq import LeadIQClient, normalize_domain, search_companies


class ProviderError(Exception):
    pass


class MockLeadIQProvider:
    source = 'mock'

    def get_companies(self, **filters):
        try:
            with (PROJECT_DIR / 'mock_leadiq_data.json').open() as source:
                return [{**row, 'source': 'mock'} for row in json.load(source)]
        except (OSError, ValueError) as exc:
            raise ProviderError('Cannot read mock_leadiq_data.json. Check the local fixture file.') from exc


class LeadIQAPIProvider:
    source = 'leadiq'

    def __init__(self, client=None, fetch_limit=100):
        self.client = client or LeadIQClient()
        self.fetch_limit = fetch_limit

    def get_companies(self, country=None, industry=None, min_employees=None, max_employees=None, limit=None):
        limit = self.fetch_limit if limit is None else limit
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("Account fetch limit must be a positive integer.")
        limit = min(limit, self.fetch_limit)
        rows = search_companies(country, min_employees, max_employees, industry, limit, client=self.client)
        result = []
        for row in rows:
            if not row.get('id'):
                raise ProviderError('LeadIQ company response has no ID. Check leadiq_schema.py against your schema.')
            result.append({'company_id': row['id'], 'company_name': row.get('name'),
                           **{key: value for key, value in row.items() if key not in {'id', 'name'}},
                           'it_headcount': None, 'number_of_locations': None,
                           'it_hiring': None, 'growth_signal': None, 'source': 'leadiq'})
        return result


class SalesforceCSVProvider:
    source = 'csv'

    def __init__(self, path=None):
        self.path = Path(path).expanduser() if path else None
        if self.path and not self.path.is_absolute():
            self.path = PROJECT_DIR / self.path
        self.warning = None

    def get_accounts(self):
        self.warning = None
        try:
            if not self.path:
                raise ValueError('SALESFORCE_CSV_PATH is not set')
            with self.path.open(newline='', encoding='utf-8-sig') as source:
                reader = csv.DictReader(source)
                expected = {'salesforce_id', 'account_name', 'domain', 'country'}
                if not expected <= set(reader.fieldnames or []):
                    raise ValueError('CSV needs salesforce_id, account_name, domain and country headers')
                accounts = []
                for row in reader:
                    if None in row or any(row.get(key) is None for key in expected):
                        raise ValueError('CSV row has missing or extra columns')
                    record = {key: row[key].strip() for key in expected}
                    if not record['account_name'] and not record['domain']:
                        raise ValueError('CSV row needs an account name or domain')
                    record['domain'] = normalize_domain(record['domain'])
                    accounts.append(record)
                return accounts  # A successfully read empty export is a valid comparison.
        except (OSError, UnicodeError, ValueError, csv.Error):
            self.warning = 'Salesforce CSV could not be loaded or validated. Check SALESFORCE_CSV_PATH and the four required columns. Accounts are NOT_CHECKED.'
            return None  # Unknown CRM coverage must never become an empty, "checked" list.


class MockSalesforceProvider(SalesforceCSVProvider):
    source = 'mock'

    def __init__(self):
        super().__init__(PROJECT_DIR / 'mock_salesforce_accounts.csv')


class DisabledSalesforceProvider:
    source = 'disabled'
    warning = 'Salesforce is disabled. Accounts are NOT_CHECKED; net-new status is unverified.'

    def get_accounts(self):
        return None


class SalesforceAPIProvider:
    def get_accounts(self):
        raise ProviderError('Salesforce API is not implemented. Select csv, mock or disabled.')


def get_providers(settings=None, client=None):
    settings = settings or get_settings()
    accounts = MockLeadIQProvider() if settings.account == 'mock' else LeadIQAPIProvider(client, settings.account_fetch_limit)
    crm = {'mock': MockSalesforceProvider, 'disabled': DisabledSalesforceProvider}
    salesforce = SalesforceCSVProvider(settings.salesforce_csv_path) if settings.salesforce == 'csv' else crm[settings.salesforce]()
    return accounts, salesforce
