"""Tests never inherit developer credentials or provider settings from .env."""
import pytest


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch, tmp_path):
    for name in ('ACCOUNT_PROVIDER', 'PROSPECT_PROVIDER', 'SALESFORCE_PROVIDER',
                 'SALESFORCE_CSV_PATH', 'LEADIQ_API_KEY', 'USE_MOCK_DATA',
                 'LEADIQ_CACHE_TTL_SECONDS', 'LEADIQ_ACCOUNT_FETCH_LIMIT', 'LEADIQ_PROSPECT_FETCH_LIMIT'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr('config.load_environment', lambda: None)
    monkeypatch.setenv('LOCAL_DB_PATH', str(tmp_path / 'data' / 'app.db'))
