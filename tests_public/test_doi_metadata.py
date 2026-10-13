"""DOI routing tests using fake HTTP responses; no network or model calls."""
import copy
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

import requests

from pipelines.clients import doi_metadata as metadata


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self.body = body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return copy.deepcopy(self.body)


def datacite_record(doi="10.48550/arxiv.2205.11894"):
    return {"data": {"id": doi, "type": "dois", "attributes": {
        "doi": doi, "titles": [{"title": "All source title text"}],
        "publicationYear": 2022, "created": "2024-01-01T00:00:00Z",
        "types": {"resourceTypeGeneral": "Preprint"},
        "relatedIdentifiers": [{"relatedIdentifier": "10.1234/published", "relationType": "IsPublishedIn"}],
    }}}


def agency_record(doi="10.1234/article", agency="crossref"):
    return {"message": {"DOI": doi, "agency": {"id": agency, "label": agency}}}


class DoiMetadataTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.runtime = SimpleNamespace(ledger=SimpleNamespace(
            event=lambda event, **kwargs: self.events.append({"event": event, **kwargs})))
        runtime_patch = patch.object(metadata, "get_runtime", return_value=self.runtime)
        runtime_patch.start()
        self.addCleanup(runtime_patch.stop)
        for target in ("requests.sessions.Session.request", "socket.create_connection", "socket.socket.connect"):
            blocker = patch(target, side_effect=AssertionError("Unexpected network in DOI offline tests"))
            blocker.start()
            self.addCleanup(blocker.stop)

    def test_arxiv_routing_ids_normalize_case_encoding_and_keep_input_immutable(self):
        cases = {
            "10.48550/arXiv.2205.11894": "2205.11894",
            " DOI:10.48550/ARXIV.2205.11894 ": "2205.11894",
            "https://doi.org/10.48550%2FarXiv.2205.11894": "2205.11894",
            "https://dx.doi.org/10.48550/arXiv.hep-th/9901001": "hep-th/9901001",
            "10.48550/arXiv.2205.11894v2": "2205.11894",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                original = value
                self.assertEqual(metadata.arxiv_doi_id(value), expected)
                self.assertEqual(value, original)
        self.assertEqual(metadata.normalize_doi("DOI:10.48550/ARXIV.2205.11894v2"),
                         "10.48550/arxiv.2205.11894v2")
        for value in (None, "2205.11894", "ARXIV:2205.11894", "10.1234/arxiv.2205.11894",
                      "10.48550/arxiv.bad", "10.48550/arxiv.2205.11894junk"):
            with self.subTest(value=value):
                self.assertIsNone(metadata.arxiv_doi_id(value))

    def test_documented_arxiv_agency_never_calls_crossref(self):
        with patch.object(metadata, "get", side_effect=AssertionError("Known prefix needs no agency request")):
            self.assertEqual(metadata.doi_agency("https://doi.org/10.48550%2FARXIV.2205.11894"), "datacite")
        self.assertEqual(self.events[-1]["status"], "ROUTED")
        self.assertFalse(self.events[-1]["network_request"])

    def test_datacite_success_cached_by_normalized_doi_and_returns_complete_copies(self):
        record = datacite_record()
        with patch.object(metadata, "get", return_value=FakeResponse(body=record)) as get:
            first = metadata.datacite_lookup("10.48550/arXiv.2205.11894")
            first["titles"][0]["title"] = "consumer edit"
            second = metadata.datacite_lookup("https://doi.org/10.48550%2FARXIV.2205.11894")
        self.assertEqual(get.call_count, 1)
        self.assertEqual(get.call_args.args[0], "https://api.datacite.org/dois/10.48550%2Farxiv.2205.11894")
        self.assertEqual(second, record["data"]["attributes"])
        self.assertEqual(second["publicationYear"], 2022)
        self.assertEqual(second["created"], "2024-01-01T00:00:00Z")
        self.assertTrue(self.events[-1]["cache_hit"])
        self.assertEqual(self.events[0]["lookup_id"], self.events[-1]["cached_from_lookup_id"])

    def test_datacite_explicit_404_is_provider_scoped_negative_cache(self):
        with patch.object(metadata, "get", return_value=FakeResponse(404)) as get:
            self.assertIsNone(metadata.datacite_lookup("10.48550/arXiv.2205.11894"))
            self.assertIsNone(metadata.datacite_lookup("DOI:10.48550/arxiv.2205.11894"))
        self.assertEqual(get.call_count, 1)
        self.assertEqual([event["status"] for event in self.events], ["NOT_FOUND", "NOT_FOUND"])
        self.assertEqual(self.events[-1]["provider_scope"], "datacite")
        self.assertEqual(self.events[-1]["scope"], "current_run")
        self.assertFalse(hasattr(self.runtime, "retrieval_issues"))

    def test_server_rate_limit_and_invalid_responses_are_not_negative_cached(self):
        for failure in (FakeResponse(500), FakeResponse(429), FakeResponse(400),
                        FakeResponse(body=ValueError("bad JSON")), FakeResponse(body={"data": []})):
            with self.subTest(status=failure.status_code, body=failure.body):
                self.runtime = SimpleNamespace(ledger=SimpleNamespace(event=lambda *a, **k: None))
                with patch.object(metadata, "get_runtime", return_value=self.runtime), \
                     patch.object(metadata, "get", side_effect=[failure, FakeResponse(body=datacite_record())]) as get:
                    self.assertIsNone(metadata.datacite_lookup("10.48550/arxiv.2205.11894"))
                    self.assertIsNotNone(metadata.datacite_lookup("10.48550/arxiv.2205.11894"))
                self.assertEqual(get.call_count, 2)

    def test_identifier_conflict_is_preserved_and_retry_is_not_suppressed(self):
        wrong = datacite_record()
        wrong["data"]["attributes"]["doi"] = "10.48550/arxiv.1111.11111"
        with patch.object(metadata, "get", side_effect=[FakeResponse(body=wrong), FakeResponse(body=datacite_record())]) as get:
            self.assertIsNone(metadata.datacite_lookup("10.48550/arxiv.2205.11894"))
            self.assertIsNotNone(metadata.datacite_lookup("10.48550/arxiv.2205.11894"))
        self.assertEqual(get.call_count, 2)
        self.assertEqual(self.events[0]["status"], "IDENTITY_CONFLICT")
        self.assertEqual(self.runtime.retrieval_issues[0]["status"], "IDENTITY_CONFLICT")

    def test_agency_success_is_cached_and_does_not_assume_only_two_agencies(self):
        with patch.object(metadata, "get", return_value=FakeResponse(body=agency_record(agency="medra"))) as get:
            self.assertEqual(metadata.doi_agency("10.1234/article"), "medra")
            self.assertEqual(metadata.doi_agency("DOI:10.1234/ARTICLE"), "medra")
        self.assertEqual(get.call_count, 1)
        self.assertEqual(get.call_args.args[0], "https://api.crossref.org/works/10.1234%2Farticle/agency")

    def test_agency_404_does_not_block_datacite(self):
        record = datacite_record("10.1234/article")
        with patch.object(metadata, "get", side_effect=[FakeResponse(404), FakeResponse(body=record)]) as get:
            self.assertIsNone(metadata.doi_agency("10.1234/article"))
            self.assertIsNone(metadata.doi_agency("10.1234/article"))
            self.assertEqual(metadata.datacite_lookup("10.1234/article"), record["data"]["attributes"])
        self.assertEqual(get.call_count, 2)
        self.assertEqual(self.events[1]["provider_scope"], "doi_agency")

    def test_agency_errors_and_conflicting_identity_are_not_cached(self):
        for failure in (FakeResponse(500), FakeResponse(body=agency_record("10.1234/other")),
                        FakeResponse(body={"message": {"DOI": "10.1234/article", "agency": []}})):
            with self.subTest(status=failure.status_code):
                runtime = SimpleNamespace(ledger=SimpleNamespace(event=lambda *a, **k: None))
                with patch.object(metadata, "get_runtime", return_value=runtime), \
                     patch.object(metadata, "get", side_effect=[failure, FakeResponse(body=agency_record())]) as get:
                    self.assertIsNone(metadata.doi_agency("10.1234/article"))
                    self.assertEqual(metadata.doi_agency("10.1234/article"), "crossref")
                self.assertEqual(get.call_count, 2)

    def test_negative_cache_does_not_cross_runtime_snapshot(self):
        with patch.object(metadata, "get", side_effect=[FakeResponse(404), FakeResponse(body=datacite_record())]) as get:
            self.assertIsNone(metadata.datacite_lookup("10.48550/arxiv.2205.11894"))
            other_run = SimpleNamespace(ledger=SimpleNamespace(event=lambda *a, **k: None))
            with patch.object(metadata, "get_runtime", return_value=other_run):
                self.assertIsNotNone(metadata.datacite_lookup("10.48550/arxiv.2205.11894"))
        self.assertEqual(get.call_count, 2)

    def test_parallel_same_run_lookup_coalesces_without_dropping_consumers(self):
        calls = []
        lock = threading.Lock()
        def fetch(*args, **kwargs):
            with lock:
                calls.append(args[0])
            time.sleep(0.01)
            return FakeResponse(body=datacite_record())
        with patch.object(metadata, "get", side_effect=fetch), ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(metadata.datacite_lookup, ["10.48550/arxiv.2205.11894"] * 8))
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(results), 8)
        self.assertTrue(all(row == datacite_record()["data"]["attributes"] for row in results))
        self.assertEqual(len(self.events), 8)

    def test_invalid_inputs_do_not_reach_http(self):
        with patch.object(metadata, "get", side_effect=AssertionError("Invalid DOI must not reach HTTP")):
            for value in (None, "", "garbage", "ARXIV:2205.11894", "10.1234/has space"):
                self.assertIsNone(metadata.datacite_lookup(value))
                self.assertIsNone(metadata.doi_agency(value))


if __name__ == "__main__":
    unittest.main()
