"""Verified LeadIQ GraphQL schema boundary (public reference, October 6, 2026).

Person has no skills or contact-availability fields; Company has no LinkedIn URL.
Keep queries and source taxonomy conversion here, never in scoring or UI.
"""
from personas import PERSONAS, normalize_text

AUTH_QUERY = 'query TestAuth { account { plans { name } } }'
COMPANY_SEARCH_QUERY = '''
query DiscoverCompanies($input: GroupedSearchInput!) {
  groupedAdvancedSearch(input: $input) {
    totalCompanies
    companies {
      company { id name domain employeeCount industry country city }
      totalContactsInCompany
    }
    after { key value }
  }
}
'''
PROSPECT_SEARCH_QUERY = '''
query DiscoverProspects($input: FlatSearchInput!) {
  flatAdvancedSearch(input: $input) {
    totalPeople
    people { id companyId name firstName lastName title seniority role country city linkedinUrl }
    after { key value }
  }
}
'''
# Local taxonomy translations, not additional GraphQL fields. Unknown values are
# preserved; missing values remain None. Head/Entry are not LeadIQ filter enums.
SENIORITY_FROM_API = {'Executive': 'C-Level', 'SeniorIndividualContributor': 'Senior'}
SENIORITY_TO_API = {'c level': 'Executive', 'vp': 'VP', 'director': 'Director',
                    'manager': 'Manager', 'senior': 'SeniorIndividualContributor'}
ROLE_FROM_API = {'InformationTechnology': 'Information Technology',
                 'Information Technology': 'Information Technology', 'IT': 'Information Technology',
                 'InformationSecurity': 'Information Security'}


def company_filter(country=None, industry=None, min_employees=None, max_employees=None,
                   company_id=None, company_name=None, domain=None):
    result = {}
    if company_id:
        result['ids'] = [company_id]
    elif domain:
        from leadiq import normalize_domain
        result['domains'] = [normalize_domain(domain)]
    elif company_name:
        result['names'] = [company_name]
    if country:
        result['locations'] = [{'country': country}]
    if industry:
        result['industries'] = [industry]
    sizes = {k: v for k, v in (('min', min_employees), ('max', max_employees)) if v is not None}
    if sizes:
        result['sizes'] = [sizes]
    return result


def contact_filter(persona=None, job_titles=(), seniorities=(), country=None):
    result = {}
    # Title filters on the API are search criteria. The local tolerant matcher
    # remains the final authority on title/persona/seniority/function fit.
    titles = list(job_titles) or list(PERSONAS.get(persona, []))
    if titles:
        result['titles'] = titles
    mapped = [SENIORITY_TO_API.get(normalize_text(s)) for s in seniorities]
    if mapped and all(mapped):
        result['seniorities'] = list(dict.fromkeys(mapped))
    if country:
        result['locations'] = [{'country': country}]
    return result


def normalize_person(person):
    first, last = person.get('firstName'), person.get('lastName')
    name = person.get('name') or ' '.join(part for part in (first, last) if part) or None
    seniority, role = person.get('seniority'), person.get('role')
    return {'prospect_id': person.get('id'), 'company_id': person.get('companyId'),
            'first_name': first, 'last_name': last, 'full_name': name,
            'job_title': person.get('title'), 'seniority': SENIORITY_FROM_API.get(seniority, seniority),
            'function': ROLE_FROM_API.get(role, role), 'country': person.get('country'),
            'city': person.get('city'), 'linkedin_url': person.get('linkedinUrl'),
            'skills': [], 'email_available': None, 'phone_available': None, 'source': 'leadiq'}
