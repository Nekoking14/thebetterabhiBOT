"""Independent provider settings, with legacy mock-mode fallback."""
import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent


def load_environment():
    load_dotenv(PROJECT_DIR / '.env', override=False)


def use_mock_data():
    """Legacy setting, used only for provider defaults when no explicit setting exists."""
    load_environment()
    value = os.getenv('USE_MOCK_DATA', 'true').strip().lower()
    if value not in ('true', 'false'):
        raise ValueError('USE_MOCK_DATA must be true or false.')
    return value == 'true'


@dataclass(frozen=True)
class ProviderSettings:
    account: str = 'mock'
    prospect: str = 'mock'
    salesforce: str = 'mock'
    salesforce_csv_path: str = ''
    cache_ttl_seconds: int = 3600
    account_fetch_limit: int = 100
    prospect_fetch_limit: int = 50

    def __post_init__(self):
        for value, options, label in ((self.account, {'mock', 'leadiq'}, 'ACCOUNT_PROVIDER'),
                                     (self.prospect, {'mock', 'leadiq'}, 'PROSPECT_PROVIDER'),
                                     (self.salesforce, {'mock', 'csv', 'disabled'}, 'SALESFORCE_PROVIDER')):
            if value not in options:
                raise ValueError(f'{label} must be one of: {", ".join(sorted(options))}. Salesforce API is not implemented.')
        if not 0 <= self.cache_ttl_seconds <= 86400:
            raise ValueError('LEADIQ_CACHE_TTL_SECONDS must be 0–86400 (0 disables caching).')
        if not 1 <= self.account_fetch_limit <= 500 or not 1 <= self.prospect_fetch_limit <= 100:
            raise ValueError('LeadIQ account fetch limit must be 1–500 and prospect fetch limit 1–100.')


def get_settings(*, include_salesforce=True):
    load_environment()
    # Explicit settings take precedence over legacy USE_MOCK_DATA.
    explicit = all(os.getenv(name, '').strip() for name in
                   (('ACCOUNT_PROVIDER', 'PROSPECT_PROVIDER', 'SALESFORCE_PROVIDER') if include_salesforce
                    else ('ACCOUNT_PROVIDER', 'PROSPECT_PROVIDER')))
    legacy = True if explicit else use_mock_data()
    try:
        return ProviderSettings(
            account=os.getenv('ACCOUNT_PROVIDER', 'mock' if legacy else 'leadiq').strip().lower(),
            prospect=os.getenv('PROSPECT_PROVIDER', 'mock' if legacy else 'leadiq').strip().lower(),
            salesforce=(os.getenv('SALESFORCE_PROVIDER', 'mock' if legacy else 'disabled').strip().lower()
                        if include_salesforce else 'disabled'),
            salesforce_csv_path=os.getenv('SALESFORCE_CSV_PATH', '').strip(),
            cache_ttl_seconds=int(os.getenv('LEADIQ_CACHE_TTL_SECONDS', '3600')),
            account_fetch_limit=int(os.getenv('LEADIQ_ACCOUNT_FETCH_LIMIT', '100')),
            prospect_fetch_limit=int(os.getenv('LEADIQ_PROSPECT_FETCH_LIMIT', '50')))
    except ValueError as exc:
        raise ValueError(f'Invalid provider configuration: {exc}') from exc


def leadiq_api_key():
    """Transient access only. Never return credentials to a UI or cache."""
    load_environment()
    value = os.getenv('LEADIQ_API_KEY', '').strip()
    return '' if value in {'your_leadiq_api_key_here', 'your_secret_base64_api_key_here'} else value


def leadiq_is_configured():
    return bool(leadiq_api_key())
