"""Offline contract checks: no real credentials or network requests."""

import os
import unittest
from unittest.mock import Mock, patch

import requests

from leadiq import LeadIQError, leadiq_request, normalize_domain, search_companies, test_auth as check_auth
from main import main


class DomainTests(unittest.TestCase):
    def test_normalization(self):
        for raw, expected in (
            ("https://www.example.com/", "example.com"),
            ("http://example.co.uk", "example.co.uk"),
            ("www.company.com", "company.com"),
            (" HTTPS://WWW.Example.COM:443/path?q=1#part ", "example.com"),
            ("app.example.com.", "app.example.com"),
            (None, None), ("", None),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_domain(raw), expected)
        with self.assertRaises(ValueError):
            normalize_domain("not a domain")


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"LEADIQ_API_KEY": "test-secret", "USE_MOCK_DATA": "false"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.dotenv = patch("config.load_environment")
        self.dotenv.start()
        self.addCleanup(self.dotenv.stop)
        self.post = patch("leadiq.requests.post")
        self.request = self.post.start()
        self.addCleanup(self.post.stop)

    def response(self, status=200, payload=None):
        response = Mock(status_code=status, ok=status < 400, headers={})
        response.json.return_value = payload if payload is not None else {"data": {"ok": True}}
        self.request.return_value = response
        return response

    def test_success_and_basic_header(self):
        self.response()
        self.assertEqual(leadiq_request("query { x }", {"x": 1}), {"ok": True})
        kwargs = self.request.call_args.kwargs
        self.assertEqual(kwargs["headers"]["Authorization"], "Basic test-secret")
        self.assertEqual(kwargs["json"]["variables"], {"x": 1})
        self.assertEqual(kwargs["timeout"], (10, 30))

    def test_missing_key_and_placeholder_do_not_request(self):
        for key in ("", "your_leadiq_api_key_here"):
            with patch.dict(os.environ, {"LEADIQ_API_KEY": key}):
                with self.assertRaisesRegex(LeadIQError, "Missing LeadIQ credentials"):
                    leadiq_request("query { x }")
                with patch("sys.stderr"), patch("sys.stdout"):
                    self.assertEqual(main(["test-auth"]), 1)
        self.request.assert_not_called()

    def test_http_failures(self):
        for status, text in ((401, "authentication"), (403, "authentication"),
                             (429, "rate limit"), (500, "HTTP error")):
            self.response(status)
            with self.subTest(status=status), self.assertRaisesRegex(LeadIQError, text):
                leadiq_request("query { x }")

    def test_network_failures(self):
        for error, text in ((requests.ConnectionError(), "Cannot connect"),
                            (requests.Timeout(), "timed out")):
            self.request.side_effect = error
            with self.assertRaisesRegex(LeadIQError, text):
                leadiq_request("query { x }")

    def test_invalid_json_and_missing_data(self):
        self.response().json.side_effect = ValueError()
        with self.assertRaisesRegex(LeadIQError, "non-JSON"):
            leadiq_request("query { x }")
        self.response(payload={"data": None})
        with self.assertRaisesRegex(LeadIQError, "missing GraphQL data"):
            leadiq_request("query { x }")

    def test_graphql_errors_and_redaction(self):
        for message, code, hint in (
            ("Cannot query field 'old'", "GRAPHQL_VALIDATION_FAILED", "schema differs"),
            ("Denied", "UNAUTHENTICATED", "permissions"),
            ("Throttled", "RATE_LIMITED", "Wait before"),
        ):
            self.response(payload={"data": {"partial": True}, "errors": [
                {"message": message + " test-secret", "extensions": {"code": code}}]})
            with self.assertRaisesRegex(LeadIQError, hint) as ctx:
                leadiq_request("query { x }")
            self.assertNotIn("test-secret", str(ctx.exception))

    def test_account_query(self):
        self.response(payload={"data": {"account": {"plans": []}}})
        self.assertEqual(check_auth(), {"plans": []})

    def test_search_mapping_and_unknown_linkedin_url(self):
        self.response(payload={"data": {"groupedAdvancedSearch": {"companies": [
            {"company": {"id": "123", "name": "Acme", "domain": "https://www.acme.co.uk/",
                         "employeeCount": 1250, "industry": "Manufacturing", "country": "United Kingdom",
                         "city": None}, "totalContactsInCompany": 0}]}}})
        result = search_companies("United Kingdom", 500, 5000, "Manufacturing", 20)
        self.assertEqual(result[0]["domain"], "acme.co.uk")
        self.assertEqual(result[0]["matching_contacts"], 0)
        self.assertIsNone(result[0]["linkedin_url"])
        self.assertEqual(self.request.call_args.kwargs["json"]["variables"]["input"], {
            "companyFilter": {"locations": [{"country": "United Kingdom"}],
                              "industries": ["Manufacturing"], "sizes": [{"min": 500, "max": 5000}]},
            "limit": 20})

    def test_invalid_filters_never_request(self):
        for kwargs in ({"limit": 0}, {"min_employees": -1},
                       {"min_employees": 5000, "max_employees": 500}):
            with self.assertRaises(ValueError):
                search_companies(**kwargs)
        self.request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
