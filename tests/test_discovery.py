"""Offline MVP regression tests, including the no-network contract."""
from unittest.mock import patch

import pytest

from discovery import discover_accounts
from leadiq import LeadIQError, leadiq_request
from main import main
from matching import (normalize_domain, normalize_company_name, exact_domain_match,
                      company_name_similarity, detect_salesforce_duplicate)
from providers import MockLeadIQProvider, MockSalesforceProvider, LeadIQAPIProvider
from scoring import score_company


@pytest.fixture
def company():
    return dict(company_id='x', company_name='Fictional Forge Limited', domain='forge.example',
                country='United Kingdom', employee_count=1200, industry='Manufacturing',
                matching_contacts=17, it_headcount=50, number_of_locations=5,
                it_hiring=True, growth_signal=True)


@pytest.mark.parametrize('value,expected', [
    ('https://www.example.com/', 'example.com'), ('http://example.co.uk', 'example.co.uk'),
    ('www.company.com', 'company.com'), (' HTTPS://WWW.Example.COM:443/path ', 'example.com'),
    ('app.example.com', 'app.example.com'), ('', None), (None, None)])
def test_domain_normalization(value, expected):
    assert normalize_domain(value) == expected


@pytest.mark.parametrize('name', [' Fictional Forge Limited ', 'FICTIONAL FORGE LTD.',
                                'Fictional Forge Ltd Limited'])
def test_company_name_normalization(name):
    assert normalize_company_name(name) == 'fictional forge'


def test_company_name_similarity():
    assert company_name_similarity('Forge Ltd', 'Forge Limited') == 100
    assert company_name_similarity('', '') == 0
    assert normalize_company_name('Café & Sons Ltd') == 'cafe and sons'


def test_exact_domain(company):
    account = dict(domain=' HTTPS://WWW.FORGE.EXAMPLE/ ', account_name='Different brand', country='Ireland')
    assert exact_domain_match(company['domain'], account['domain'])
    assert detect_salesforce_duplicate(company, [account]) == 'EXISTS'
    assert not exact_domain_match(None, '')


def test_possible_name_match_with_geography(company):
    account = dict(domain='old-forge.example', account_name='Fictional Forge Ltd', country='UK')
    assert detect_salesforce_duplicate(company, [account]) == 'POSSIBLE_MATCH'
    account['country'] = 'Ireland'
    assert detect_salesforce_duplicate(company, [account]) == 'NET_NEW'
    account['country'] = ''
    assert detect_salesforce_duplicate(company, [account]) == 'NET_NEW'


def test_genuine_net_new_and_weak_name(company):
    assert detect_salesforce_duplicate(company, [dict(domain='different.example',
        account_name='Other Forge Industries', country='United Kingdom')]) == 'NET_NEW'
    assert detect_salesforce_duplicate(company, []) == 'NET_NEW'


def test_domain_priority_across_accounts(company):
    accounts = [dict(domain='old.example', account_name='Fictional Forge Ltd', country='UK'),
                dict(domain='forge.example', account_name='Rebranded Inc', country='Ireland')]
    assert detect_salesforce_duplicate(company, accounts) == 'EXISTS'


def test_full_icp_score(company):
    score = score_company(company)
    assert score['total_score'] == 100
    assert score['components'] == dict(employee_size=25, industry=20, geography=10,
                                      it_headcount=15, locations=10, it_hiring=10, growth_signal=10)
    assert len(score['reasons']) == 7


def test_partial_score_and_unknown_signals(company):
    company.update(it_headcount=20, number_of_locations=2, it_hiring=None, growth_signal=False)
    assert score_company(company)['total_score'] == 70
    assert score_company({})['total_score'] == 0
    # A string 'false' or 'true' is not a verified boolean hiring signal.
    assert score_company({'it_hiring': 'true'})['components']['it_hiring'] == 0


@pytest.mark.parametrize('employees,points', [(499, 0), (500, 25), (5000, 25), (5001, 0)])
def test_employee_score_boundaries(company, employees, points):
    company['employee_count'] = employees
    assert score_company(company)['components']['employee_size'] == points


def test_filtering_and_sorting(company):
    low = {**company, 'company_id': 'low', 'company_name': 'Low Fit', 'it_hiring': False}
    elsewhere = {**company, 'company_id': 'ie', 'country': 'Ireland'}
    small = {**company, 'company_id': 'small', 'employee_count': 20}
    other = {**company, 'company_id': 'other', 'industry': 'Retail'}
    result = discover_accounts([low, elsewhere, small, other, company], [], country='uk',
                               industry='manufacturing', min_employees=500, max_employees=5000,
                               min_score=90, limit=1)
    assert result['summary']['total_candidates'] == 2
    assert result['eligible_results'] == 2
    assert result['results'][0]['company_id'] == 'x'
    assert [r['score']['total_score'] for r in discover_accounts([low, company], [])['results']] == [100, 90]


def test_default_status_filter_and_summary(company):
    existing = {**company, 'company_id': 'exists', 'domain': 'exists.example'}
    possible = {**company, 'company_id': 'possible', 'domain': 'other.example', 'company_name': 'Possible Forge Ltd'}
    accounts = [dict(domain='exists.example', account_name='Existing', country='UK'),
                dict(domain='legacy.example', account_name='Possible Forge Limited', country='UK')]
    result = discover_accounts([company, existing, possible], accounts)
    assert [r['salesforce_status'] for r in result['results']] == ['NET_NEW']
    assert result['summary'] == {'total_candidates': 3, 'EXISTS': 1, 'POSSIBLE_MATCH': 1, 'NET_NEW': 1}
    assert len(discover_accounts([company, existing, possible], accounts, show_all=True)['results']) == 3
    assert not discover_accounts([company], [], min_score=100, country='Ireland')['results']


@pytest.mark.parametrize('kwargs', [{'limit': 0}, {'min_score': 101}, {'min_score': -1},
                                   {'min_employees': -1}, {'min_employees': 50, 'max_employees': 10}])
def test_bad_filters(company, kwargs):
    with pytest.raises(ValueError):
        discover_accounts([company], [], **kwargs)


def test_fixtures():
    companies = MockLeadIQProvider().get_companies()
    accounts = MockSalesforceProvider().get_accounts()
    assert len(companies) >= 40
    assert 15 <= len(accounts) <= 20
    assert len({row['company_id'] for row in companies}) == len(companies)
    result = discover_accounts(companies, accounts, show_all=True, limit=100)
    assert result['summary'] == {'total_candidates': 44, 'EXISTS': 16, 'POSSIBLE_MATCH': 4, 'NET_NEW': 24}


def test_mock_mode_never_contacts_api(monkeypatch, capsys):
    monkeypatch.setenv('USE_MOCK_DATA', 'true')
    monkeypatch.delenv('LEADIQ_API_KEY', raising=False)
    with patch('requests.sessions.Session.request', side_effect=AssertionError('Network forbidden')):
        assert main(['search', '--limit', '2']) == 0
        assert main(['test-auth']) == 0
        with pytest.raises(LeadIQError, match='disabled'):
            leadiq_request('query { account { plans { name } } }')
    output = capsys.readouterr().out
    assert 'MOCK DATA' in output
    assert 'Net-new accounts: 24' in output


def test_live_pipeline_fails_before_leadiq(monkeypatch, capsys):
    monkeypatch.setenv('USE_MOCK_DATA', 'false')
    with patch('providers.search_companies', side_effect=AssertionError('Must not call API')):
        assert main(['search']) == 1
    assert 'Live Salesforce integration is not implemented' in capsys.readouterr().err


def test_live_adapter_uses_canonical_contract(company):
    row = {'id': company['company_id'], 'name': company['company_name'],
           'domain': company['domain'], 'employee_count': company['employee_count'],
           'industry': company['industry'], 'country': company['country'], 'city': None,
           'matching_contacts': 17, 'linkedin_url': None}
    with patch('providers.search_companies', return_value=[row]):
        live = LeadIQAPIProvider().get_companies()
    assert live[0]['company_id'] == company['company_id']
    assert live[0]['it_headcount'] is None
    assert discover_accounts(live, [])['results'][0]['score']['total_score'] == 55


def test_configuration_rejects_typos(monkeypatch, capsys):
    monkeypatch.setenv('USE_MOCK_DATA', 'treu')
    assert main(['search']) == 1
    assert 'must be true or false' in capsys.readouterr().err
