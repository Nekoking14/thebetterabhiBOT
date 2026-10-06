"""Live-provider integration tests use synthetic HTTP responses, never real keys."""
import json
from dataclasses import replace
from uuid import uuid4
from unittest.mock import Mock, patch

import pytest
import requests
from streamlit.testing.v1 import AppTest

from config import PROJECT_DIR, ProviderSettings, get_settings
from discovery import discover_accounts
from leadiq import LeadIQClient, LeadIQError, leadiq_request
from main import main
from prospect_discovery import ProspectFilters
from prospect_providers import LeadIQProspectAPIProvider
from prospect_scoring import score_prospect
from providers import DisabledSalesforceProvider, LeadIQAPIProvider, SalesforceCSVProvider
from response_cache import ResponseCache
from service import create_service


def http_response(data=None, status=200, errors=None):
    response = Mock(status_code=status, ok=status < 400, headers={})
    response.json.return_value = {'errors': errors} if errors else {'data': data}
    return response


def company_payload(domain='https://www.forge.example/', name='Test Forge'):
    return {'groupedAdvancedSearch': {'totalCompanies': 1, 'companies': [
        {'company': {'id': 'live-c1', 'name': name, 'domain': domain, 'employeeCount': 1200,
                     'industry': 'Manufacturing', 'country': 'United Kingdom', 'city': 'Manchester'},
         'totalContactsInCompany': 18}], 'after': None}}


def people_payload():
    return {'flatAdvancedSearch': {'totalPeople': 3, 'people': [
        {'id': 'p-low', 'companyId': 'live-c1', 'name': 'Demo Support', 'title': 'IT Support Specialist',
         'seniority': 'Other', 'role': 'InformationTechnology', 'country': 'United Kingdom'},
        {'id': 'p-high', 'companyId': 'live-c1', 'firstName': 'Demo', 'lastName': 'Director',
         'title': 'IT Director', 'seniority': 'Director', 'role': 'InformationTechnology',
         'country': 'United Kingdom', 'linkedinUrl': 'https://www.linkedin.com/in/test-profile'},
        {'id': 'wrong-company', 'companyId': 'another', 'name': 'Other Employer', 'title': 'CIO'}], 'after': None}}


@pytest.fixture
def credential(monkeypatch):
    # Random, nonfunctional test marker, not a credential.
    value = str(uuid4())
    monkeypatch.setenv('LEADIQ_API_KEY', value)
    return value


def test_preferred_and_legacy_configuration(monkeypatch):
    assert get_settings() == ProviderSettings()
    monkeypatch.setenv('USE_MOCK_DATA', 'false')
    assert get_settings().account == 'leadiq'
    assert get_settings().salesforce == 'disabled'
    monkeypatch.setenv('ACCOUNT_PROVIDER', 'mock')
    monkeypatch.setenv('PROSPECT_PROVIDER', 'mock')
    monkeypatch.setenv('SALESFORCE_PROVIDER', 'csv')
    assert get_settings().account == 'mock'
    assert get_settings().salesforce == 'csv'
    monkeypatch.setenv('SALESFORCE_PROVIDER', 'api')
    with pytest.raises(ValueError, match='not implemented'):
        get_settings()


def test_live_account_normalization_and_filters(credential):
    with patch('requests.post', return_value=http_response(company_payload())) as post:
        records = LeadIQAPIProvider().get_companies(country='United Kingdom', industry='Manufacturing',
                                                   min_employees=500, max_employees=5000, limit=10)
    record = records[0]
    assert record['company_id'] == 'live-c1'
    assert record['company_name'] == 'Test Forge'
    assert record['domain'] == 'forge.example'
    assert record['source'] == 'leadiq'
    assert record['it_headcount'] is None and record['linkedin_url'] is None
    assert post.call_args.kwargs['json']['variables']['input'] == {
        'companyFilter': {'locations': [{'country': 'United Kingdom'}], 'industries': ['Manufacturing'],
                          'sizes': [{'min': 500, 'max': 5000}]}, 'limit': 10}
    result = discover_accounts(records, None)
    assert result['results'][0]['score']['total_score'] == 55
    assert result['results'][0]['salesforce_status'] == 'NOT_CHECKED'


def test_account_missing_domain_and_optional_fields(credential):
    data = company_payload(domain=None, name=None)
    data['groupedAdvancedSearch']['companies'][0]['company'] = {'id': 'live-c1'}
    with patch('requests.post', return_value=http_response(data)):
        company = LeadIQAPIProvider().get_companies()[0]
    assert company['domain'] is None and company['employee_count'] is None
    assert discover_accounts([company], None)['results'][0]['score']['total_score'] == 0


def test_live_prospect_normalization_missing_fields_and_scoring(credential):
    with patch('requests.post', return_value=http_response(people_payload())) as post:
        people = LeadIQProspectAPIProvider().get_prospects('live-c1')
    assert len(people) == 2
    assert post.call_args.kwargs['json']['variables']['input']['companyFilter'] == {'ids': ['live-c1']}
    director = next(p for p in people if p['prospect_id'] == 'p-high')
    assert director['full_name'] == 'Demo Director'
    assert director['function'] == 'Information Technology'
    assert director['skills'] == []
    assert director['email_available'] is None and director['phone_available'] is None
    assert director['source'] == 'leadiq'
    assert score_prospect(director)['total_score'] == 77
    with patch('requests.post', return_value=http_response({'flatAdvancedSearch': {
            'people': [{'id': 'missing-fields', 'companyId': 'live-c1'}]}})):
        sparse = LeadIQProspectAPIProvider().get_prospects('live-c1')[0]
    assert sparse['job_title'] is None and sparse['seniority'] is None
    assert sparse['function'] is None and sparse['linkedin_url'] is None
    assert score_prospect(sparse)['total_score'] == 0


def test_people_search_across_criteria(credential):
    with patch('requests.post', return_value=http_response(people_payload())) as post:
        result = LeadIQProspectAPIProvider().search_prospects(domain='https://www.forge.example',
            persona='IT Decision Makers', job_titles=['Director of IT'], seniorities=['Director'],
            functions=['Information Technology'], country='United Kingdom', limit=10)
    criteria = post.call_args.kwargs['json']['variables']['input']
    assert criteria['companyFilter'] == {'domains': ['forge.example']}
    assert criteria['contactFilter'] == {'titles': ['Director of IT'], 'seniorities': ['Director'],
                                         'locations': [{'country': 'United Kingdom'}]}
    assert [p['prospect_id'] for p in result] == ['p-high']


def test_unsupported_head_enum_stays_local(credential):
    with patch('requests.post', return_value=http_response(people_payload())) as post:
        LeadIQProspectAPIProvider().search_prospects(seniorities=['Head'])
    assert 'seniorities' not in post.call_args.kwargs['json']['variables']['input']['contactFilter']


def test_find_better_and_cache_are_staged(credential):
    service = create_service(ProviderSettings(account='leadiq', prospect='leadiq', salesforce='disabled'))
    with patch('requests.post', side_effect=[http_response(company_payload()), http_response(people_payload())]) as post:
        accounts = service.search_accounts()
        assert post.call_count == 1
        assert service.current_prospect('live-c1') is None
        assert post.call_count == 1
        account_score = accounts['results'][0]['score']
        best = service.find_better_prospects('live-c1', ProspectFilters(limit=3))
        assert best['results'][0]['prospect_id'] == 'p-high'
        assert post.call_count == 2
        service.find_better_prospects('live-c1', ProspectFilters(limit=6))
        service.search_accounts()
        assert post.call_count == 2
        assert accounts['results'][0]['score'] == account_score


@pytest.mark.parametrize('kind', ['missing_key', 'authentication', 'timeout', 'schema', 'rate_limit', 'credits'])
def test_errors_are_useful_and_never_cached(kind, monkeypatch, credential):
    client = LeadIQClient()
    if kind == 'missing_key':
        monkeypatch.delenv('LEADIQ_API_KEY')
    responses = {
        'authentication': http_response(status=401),
        'schema': http_response(errors=[{'message': 'Cannot query field missing', 'extensions': {'code': 'GRAPHQL_VALIDATION_FAILED'}}]),
        'rate_limit': http_response(status=429),
        'credits': http_response(errors=[{'message': 'Insufficient credits'}])}
    with patch('requests.post', side_effect=requests.Timeout() if kind == 'timeout' else None,
               return_value=responses.get(kind)) as post:
        for _ in range(2):
            with pytest.raises(LeadIQError) as err:
                client.request('query Demo { account { plans { name } } }')
            assert err.value.code == kind
        assert post.call_count == (0 if kind == 'missing_key' else 2)
        assert not client.cache._entries


def test_cache_ttl_copy_and_zero_disable():
    now = [0]
    cache = ResponseCache(ttl_seconds=5, clock=lambda: now[0])
    loader = Mock(return_value={'items': [1]})
    cache.get_or_load('query', {'limit': 1}, loader)['items'].append(2)
    assert cache.get_or_load('query', {'limit': 1}, loader) == {'items': [1]}
    assert loader.call_count == 1
    now[0] = 6
    cache.get_or_load('query', {'limit': 1}, loader)
    assert loader.call_count == 2
    cache = ResponseCache(ttl_seconds=0)
    cache.get_or_load('query', {}, loader)
    cache.get_or_load('query', {}, loader)
    assert not cache._entries


def test_credentials_are_not_in_cache_or_logs(monkeypatch, credential, caplog):
    client = LeadIQClient()
    with patch('requests.post', return_value=http_response({'account': {'plans': []}})) as post:
        client.request('query Example', {'limit': 1})
        assert credential not in repr(client.cache._entries)
        monkeypatch.setenv('LEADIQ_API_KEY', str(uuid4()))
        client.request('query Example', {'limit': 1})
        assert post.call_count == 2  # Credential rotation invalidates payload cache.
    with patch('requests.post', return_value=http_response(errors=[{'message': 'Denied ' + credential}])):
        monkeypatch.setenv('LEADIQ_API_KEY', credential)
        with pytest.raises(LeadIQError) as exc:
            client.request('query Failure')
    assert credential not in str(exc.value)
    assert credential not in caplog.text
    assert 'Authorization' not in caplog.text


def test_disabled_and_failed_csv_are_not_checked(tmp_path):
    records = [{'company_id': 'a', 'company_name': 'Example', 'domain': None}]
    assert discover_accounts(records, DisabledSalesforceProvider().get_accounts())['results'][0]['salesforce_status'] == 'NOT_CHECKED'
    for content in (None, 'name,domain\nExample,example.test\n', 'salesforce_id,account_name,domain,country\n1,Example\n'):
        path = tmp_path / 'crm.csv'
        if content is not None:
            path.write_text(content)
        provider = SalesforceCSVProvider(path)
        assert provider.get_accounts() is None
        assert provider.warning
    result = discover_accounts(records, None, salesforce_statuses=['NET_NEW'])
    assert result['summary']['NET_NEW'] == 0 and not result['results']


def test_csv_comparison_and_empty_successful_export(tmp_path):
    path = tmp_path / 'crm.csv'
    path.write_text('salesforce_id,account_name,domain,country\nsf1,Forge Ltd,HTTPS://WWW.FORGE.EXAMPLE/,United Kingdom\n')
    provider = SalesforceCSVProvider(path)
    company = {'company_id': 'a', 'company_name': 'Forge Limited', 'domain': 'forge.example', 'country': 'United Kingdom'}
    result = discover_accounts([company], provider.get_accounts(), show_all=True)
    assert result['results'][0]['salesforce_status'] == 'EXISTS'
    path.write_text('salesforce_id,account_name,domain,country\n')
    result = discover_accounts([company], provider.get_accounts())
    assert result['results'][0]['salesforce_status'] == 'NET_NEW'
    assert not provider.warning


def test_empty_live_results_are_handled(credential):
    with patch('requests.post', return_value=http_response({'flatAdvancedSearch': {'people': []}})):
        assert LeadIQProspectAPIProvider().get_prospects('any') == []
    with patch('requests.post', return_value=http_response({'groupedAdvancedSearch': {'companies': []}})):
        assert LeadIQAPIProvider().get_companies() == []


def test_explicit_live_cli_missing_key_never_calls_http(capsys):
    with patch('requests.post', side_effect=AssertionError('HTTP forbidden')):
        assert main(['test-auth', '--provider', 'leadiq']) == 1
        assert main(['search', '--provider', 'leadiq', '--limit', '10']) == 1
        assert main(['prospects', '--provider', 'leadiq', '--company-id', 'any', '--limit', '10']) == 1
    assert 'credentials' in capsys.readouterr().err


def test_documented_placeholder_and_zero_limit_never_request(monkeypatch):
    monkeypatch.setenv('LEADIQ_API_KEY', 'your_secret_base64_api_key_here')
    with patch('requests.post', side_effect=AssertionError('No request')):
        with pytest.raises(LeadIQError, match='not configured'):
            LeadIQAPIProvider().get_companies()
        with pytest.raises(ValueError, match='positive'):
            LeadIQAPIProvider().get_companies(limit=0)


def test_ui_salesforce_disabled(monkeypatch):
    monkeypatch.setenv('SALESFORCE_PROVIDER', 'disabled')
    with patch('requests.sessions.Session.request', side_effect=AssertionError('No network')):
        app = AppTest.from_file(str(PROJECT_DIR / 'app.py')).run()
    assert not app.exception
    assert next(m for m in app.metric if m.label == 'Accounts').value == '0'
    assert not any('Salesforce' in s.label for s in app.selectbox)


def test_ui_live_requests_only_on_click_and_cached(monkeypatch, credential):
    for name, value in [('ACCOUNT_PROVIDER', 'leadiq'), ('PROSPECT_PROVIDER', 'leadiq'), ('SALESFORCE_PROVIDER', 'disabled')]:
        monkeypatch.setenv(name, value)
    with patch('requests.post', side_effect=[http_response(company_payload()), http_response(people_payload())]) as post:
        app = AppTest.from_file(str(PROJECT_DIR / 'app.py')).run()
        assert not app.exception
        assert post.call_count == 0
        app.radio(key='page').set_value('Account Discovery').run()
        next(b for b in app.button if b.label == 'Find Accounts').click().run()
        assert not app.exception
        assert post.call_count == 2 and len(app.dataframe) == 1
        from repositories import ScrubRepository
        repo = ScrubRepository()
        session = repo.ensure_session()['session_id']
        account = repo.accounts(session)[0]
        app.selectbox(key=f'review_{session}').set_value(account['id']).run()
        assert not app.exception
        assert post.call_count == 2 and len(account['prospects']) == 2
        app.button(key=f"find_{account['id']}").click().run()
        assert not app.exception and post.call_count == 2
        assert any('Account Score: 55 / 100' in m.value for m in app.markdown)
        assert any('LeadIQ: Connected' in c.value for c in app.caption)


def test_failed_csv_service_warns_without_net_new(tmp_path):
    service = create_service(ProviderSettings(salesforce='csv', salesforce_csv_path=str(tmp_path / 'missing.csv')))
    result = service.search_accounts()
    assert result['summary']['NET_NEW'] == 0
    assert result['summary']['NOT_CHECKED'] == 44
    assert result['warnings']
    assert all(r['salesforce_status'] == 'NOT_CHECKED' for r in result['results'])


def test_live_invalid_filters_never_spend_requests(credential):
    service = create_service(ProviderSettings(account='leadiq', prospect='leadiq', salesforce='disabled'))
    with patch('requests.post', side_effect=AssertionError('No request')):
        with pytest.raises(ValueError, match='Minimum employees'):
            service.search_accounts(min_employees=5000, max_employees=500)


def test_success_after_authentication_failure_is_not_blocked(credential):
    client = LeadIQClient()
    with patch('requests.post', side_effect=[http_response(status=401), http_response({'account': {'plans': []}})]) as post:
        with pytest.raises(LeadIQError):
            client.request('query Account')
        assert client.request('query Account') == {'account': {'plans': []}}
        assert post.call_count == 2 and client.status == 'Connected'
