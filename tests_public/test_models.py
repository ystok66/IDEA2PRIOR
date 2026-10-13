"""Model catalog contract tests; all HTTP sessions are mocked."""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import MagicMock, patch

import requests

from paperarena import APIProfile
from paperarena.model_catalog import GEMINI_MODELS_URL, ModelCatalogError, fetch_models


def response(payload, status=200):
    value = MagicMock()
    value.status_code = status
    value.content = json.dumps(payload).encode("utf-8")
    value.json.return_value = payload
    return value


class ModelCatalogTests(unittest.TestCase):
    def session(self, replies):
        value = MagicMock()
        value.get.side_effect = replies
        return value

    def test_openai_and_custom_catalogs_use_only_selected_endpoint(self):
        profiles = [APIProfile("openai", "model", "chosen-key"),
                    APIProfile("custom", "model", "chosen-key", "http://127.0.0.1:9090/v1")]
        for profile, expected in zip(profiles, ["https://api.openai.com/v1/models", "http://127.0.0.1:9090/v1/models"]):
            session = self.session([response({"data": [{"id": "model-z"}, {"id": "model-a"}, {"id": "model-a"}, {"id": ""}]})])
            with patch("paperarena.model_catalog.requests.Session", return_value=session):
                options = fetch_models(profile)
            self.assertEqual(options, [{"id": "model-a", "label": "model-a"}, {"id": "model-z", "label": "model-z"}])
            self.assertEqual(session.get.call_count, 1)
            args, kwargs = session.get.call_args
            self.assertEqual(args[0], expected)
            self.assertEqual(kwargs["headers"]["Authorization"], "Bearer chosen-key")
            self.assertNotIn("chosen-key", args[0])
            self.assertEqual(kwargs["params"], {})
            self.assertFalse(kwargs["allow_redirects"])
            self.assertFalse(session.trust_env)
            self.assertEqual(session.mount.call_args_list[0].args[1].max_retries.total, 0)
            session.post.assert_not_called()
            session.close.assert_called_once()

    def test_gemini_filter_header_and_bounded_pagination(self):
        session = self.session([
            response({"models": [
                {"name": "models/gemini-3.8-flash", "displayName": "Gemini Flash", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/embedding-only", "supportedGenerationMethods": ["embedContent"]}], "nextPageToken": "page-two"}),
            response({"models": [{"name": "models/gemini-3.1-flash-lite-preview", "supportedGenerationMethods": ["generateContent", "countTokens"]}]})])
        with patch("paperarena.model_catalog.requests.Session", return_value=session):
            options = fetch_models(APIProfile("gemini", "model", "google-key"))
        self.assertEqual([row["id"] for row in options], ["gemini-3.1-flash-lite-preview", "gemini-3.8-flash"])
        self.assertEqual(session.get.call_count, 2)
        for call in session.get.call_args_list:
            self.assertEqual(call.args[0], GEMINI_MODELS_URL)
            self.assertEqual(call.kwargs["headers"]["x-goog-api-key"], "google-key")
            self.assertNotIn("key", call.kwargs["params"])
        self.assertEqual(session.get.call_args_list[1].kwargs["params"], {"pageSize": 1000, "pageToken": "page-two"})
        session.post.assert_not_called()

    def test_optional_compatible_cursor_stays_on_same_endpoint(self):
        session = self.session([response({"data": [{"id": "first"}], "has_more": True, "last_id": "first"}), response({"data": [{"id": "second"}]})])
        with patch("paperarena.model_catalog.requests.Session", return_value=session):
            self.assertEqual(len(fetch_models(APIProfile("custom", "model", "test-key", "https://example.invalid/v1"))), 2)
        self.assertEqual(session.get.call_args_list[1].kwargs["params"], {"after": "first"})
        self.assertEqual(len({call.args[0] for call in session.get.call_args_list}), 1)

    def test_missing_key_fails_before_session_created(self):
        with patch("paperarena.model_catalog.requests.Session") as constructor:
            with self.assertRaises(ModelCatalogError) as failure:
                fetch_models(APIProfile("gemini", "model"))
        self.assertEqual(failure.exception.code, "MISSING_API_KEY")
        constructor.assert_not_called()

    def test_env_key_is_explicitly_resolved(self):
        session = self.session([response({"data": []})])
        with patch.dict(os.environ, {"CATALOG_TEST_KEY": "selected-env-key"}), patch("paperarena.model_catalog.requests.Session", return_value=session):
            self.assertEqual(fetch_models(APIProfile("openai", "model", api_key_env="CATALOG_TEST_KEY")), [])
        self.assertEqual(session.get.call_args.kwargs["headers"]["Authorization"], "Bearer selected-env-key")

    def test_http_error_does_not_expose_provider_payload_or_follow_redirect(self):
        session = self.session([response({"error": "secret-key https://private.invalid user material"}, status=302)])
        with patch("paperarena.model_catalog.requests.Session", return_value=session), self.assertRaises(ModelCatalogError) as failure:
            fetch_models(APIProfile("openai", "model", "secret-key"))
        self.assertEqual(failure.exception.code, "CATALOG_HTTP_ERROR")
        self.assertNotIn("secret-key", str(failure.exception))
        self.assertNotIn("private.invalid", str(failure.exception))
        self.assertEqual(session.get.call_count, 1)

    def test_timeout_and_transport_failure_have_no_retries_or_sensitive_errors(self):
        for exception, code in [(requests.Timeout("secret-key timeout"), "CATALOG_TIMEOUT"), (requests.ConnectionError("secret-key connection"), "CATALOG_REQUEST_FAILED")]:
            session = self.session([exception])
            with patch("paperarena.model_catalog.requests.Session", return_value=session), self.assertRaises(ModelCatalogError) as failure:
                fetch_models(APIProfile("openai", "model", "secret-key"))
            self.assertEqual(failure.exception.code, code)
            self.assertNotIn("secret-key", str(failure.exception))
            self.assertEqual(session.get.call_count, 1)
            session.close.assert_called_once()

    def test_page_limit_and_repeated_page_token_stop(self):
        payload = {"models": [], "nextPageToken": "same-token"}
        for limit, count, code in [(1, 1, "CATALOG_PAGE_LIMIT"), (5, 2, "INVALID_PAGINATION")]:
            session = self.session([response(payload), response(payload)])
            with patch("paperarena.model_catalog.requests.Session", return_value=session), self.assertRaises(ModelCatalogError) as failure:
                fetch_models(APIProfile("gemini", "model", "test-key"), max_pages=limit)
            self.assertEqual(session.get.call_count, count)
            self.assertEqual(failure.exception.code, code)

    def test_invalid_schema_and_secret_in_model_rows(self):
        session = self.session([response({"data": [{"id": "secret-key"}, {"id": "safe-model", "name": "label secret-key"}]})])
        with patch("paperarena.model_catalog.requests.Session", return_value=session):
            options = fetch_models(APIProfile("openai", "model", "secret-key"))
        self.assertEqual(options, [{"id": "safe-model", "label": "label [REDACTED]"}])
        session = self.session([response({"error": "unsupported endpoint"})])
        with patch("paperarena.model_catalog.requests.Session", return_value=session), self.assertRaises(ModelCatalogError) as failure:
            fetch_models(APIProfile("openai", "model", "test-key"))
        self.assertEqual(failure.exception.code, "INVALID_CATALOG")


if __name__ == "__main__":
    unittest.main()
