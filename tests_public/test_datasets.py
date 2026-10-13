"""Offline integrity and evidence-boundary tests for the public benchmark tiers."""
from __future__ import annotations

import importlib.util
import json
import hashlib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("public_evaluate", ROOT / "scripts" / "evaluate_public_results.py")
EVALUATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVALUATE)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


class PublicDatasetTests(unittest.TestCase):
    def test_all_files_match_manifests_and_prompt_queries(self):
        for tier, prefix in [("easy", "N"), ("medium", "C"), ("hard", "C")]:
            with self.subTest(tier=tier):
                folder = ROOT / "datasets" / tier
                manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
                rows = read_jsonl(folder / "queries.jsonl")
                gold = read_jsonl(folder / "gold.jsonl")
                self.assertEqual(manifest["difficulty"], tier.upper())
                self.assertEqual(manifest["dataset_name"], tier.upper())
                self.assertEqual(len(rows), 30)
                self.assertEqual({row["item_id"] for row in rows}, {f"{prefix}{i:03}" for i in range(1, 31)})
                self.assertEqual({row["item_id"] for row in rows}, {row["item_id"] for row in gold})
                for name, expected in manifest["files_sha256"].items():
                    self.assertEqual(hashlib.sha256((folder / name).read_bytes()).hexdigest(), expected, name)
                for row in rows:
                    self.assertFalse({"title", "doi", "arxiv_id", "gold", "answer"} & set(row))
                    prompt = (folder / row["prompt_file"]).read_bytes()
                    self.assertEqual(hashlib.sha256(prompt).hexdigest(), row["prompt_sha256"])
                    self.assertEqual(hashlib.sha256(row["query"].encode("utf-8")).hexdigest(), row["query_sha256"])
                    self.assertEqual(prompt.decode("utf-8-sig").rsplit("Research idea:", 1)[1].strip(), row["query"])
                    self.assertNotIn("\ufffd", row["query"])
                self.assertEqual(len(list((folder / "prompts").glob("*.txt"))), 30)

    def test_medium_and_hard_are_same_targets_and_distinct_queries(self):
        medium = {row["item_id"]: row for row in read_jsonl(ROOT / "datasets" / "medium" / "queries.jsonl")}
        hard = {row["item_id"]: row for row in read_jsonl(ROOT / "datasets" / "hard" / "queries.jsonl")}
        self.assertEqual(set(medium), set(hard))
        for item_id in medium:
            self.assertNotEqual(medium[item_id]["query_sha256"], hard[item_id]["query_sha256"])
        medium_gold = read_jsonl(ROOT / "datasets" / "medium" / "gold.jsonl")
        hard_gold = read_jsonl(ROOT / "datasets" / "hard" / "gold.jsonl")
        self.assertEqual([(row["item_id"], row["title"], row.get("doi")) for row in medium_gold],
                         [(row["item_id"], row["title"], row.get("doi")) for row in hard_gold])

    def test_matcher_preserves_identity_and_version_boundaries(self):
        gold = {"title": "A target paper", "doi": "10.1234/target", "accepted_arxiv_ids": ["2101.00001"],
                "family_aliases": [{"title": "Conference predecessor", "url": "https://doi.org/10.1234/conference"}]}
        self.assertEqual(EVALUATE.match_candidate({"title": "Different display title", "doi": "https://doi.org/10.1234/TARGET"}, gold), "doi")
        self.assertIsNone(EVALUATE.match_candidate({"title": "A target paper", "doi": "10.1234/different"}, gold))
        self.assertIsNone(EVALUATE.match_candidate({"title": "A closely related target paper"}, gold))
        self.assertEqual(EVALUATE.match_candidate({"arxiv_id": "https://arxiv.org/abs/2101.00001v3"}, gold), "accepted_arxiv_record")
        self.assertEqual(EVALUATE.normalize_arxiv("https://arxiv.org/pdf/2101.00001v3.pdf"), "2101.00001")
        self.assertEqual(EVALUATE.normalize_arxiv("https://doi.org/10.1234/2101.00001"), "")
        self.assertIsNone(EVALUATE.match_candidate({"title": "Conference predecessor", "doi": "10.1234/conference"}, gold))
        self.assertEqual(EVALUATE.match_candidate({"title": "Conference predecessor", "doi": "10.1234/conference"}, gold, "family"), "family_alias_doi")

    def test_missing_and_operational_failures_are_not_imputed_successes(self):
        gold = {item: {"title": "Target " + item} for item in ["A", "B", "C"]}
        rows = [{"item_id": "A", "status": "completed", "top5": [{"title": "Target A"}]},
                {"item_id": "B", "status": "error", "top5": []}]
        report = EVALUATE.score(rows, gold, ["A", "B", "C"])
        self.assertEqual(report["hits"], 1)
        self.assertEqual(report["not_run"], 1)
        self.assertEqual(report["operational_errors"], 1)
        self.assertEqual(report["completed_metrics"]["hit_at_5"], 1)
        self.assertEqual(report["end_to_end_attempted_metrics"]["hit_at_5"], 0.5)
        self.assertIsNone(report["headline_metrics"])
        self.assertIsNone(report["outcomes"][2]["target_rank"])

    def test_accepted_arxiv_doi_does_not_mask_conflicting_publication_identifiers(self):
        gold = {"title": "Target", "doi": "10.1234/published", "accepted_arxiv_ids": ["2309.11271"]}
        record_url = "https://doi.org/10.48550/arXiv.2309.11271"
        self.assertEqual(EVALUATE.match_candidate({"title": "Target", "url": record_url}, gold), "accepted_arxiv_record")
        self.assertEqual(EVALUATE.match_candidate({"doi": "10.48550/arxiv.2309.11271v1"}, gold), "accepted_arxiv_record")
        self.assertIsNone(EVALUATE.match_candidate({"title": "Target", "url": "https://doi.org/10.48550/arxiv.2309.11272"}, gold))
        self.assertIsNone(EVALUATE.match_candidate({"title": "Target", "url": record_url}, {"title": "Target", "doi": "10.1234/published"}))
        self.assertIsNone(EVALUATE.match_candidate({"title": "Target", "doi": "10.1234/unrelated", "url": record_url}, gold))
        self.assertIsNone(EVALUATE.match_candidate({"title": "Target", "doi": "10.48550/not-arxiv.2309.11271"}, gold))

    def test_reject_duplicate_results_and_six_candidate_lists(self):
        gold = {"A": {"title": "Target"}}
        with self.assertRaises(ValueError):
            EVALUATE.score([{"item_id": "A"}, {"item_id": "A"}], gold, ["A"])
        with self.assertRaises(ValueError):
            EVALUATE.score([{"item_id": "A", "top5": [{"title": "x"}] * 6}], gold, ["A"])

    def test_backend_statuses_preserve_partial_and_empty_failures(self):
        gold = {item: {"title": "Target"} for item in ["A", "B", "C"]}
        rows = [{"item_id": "A", "status": "PARTIAL", "top5": [{"title": "Target"}]},
                {"item_id": "B", "status": "PARTIAL", "top5": [{"title": "Target"}] + [{"title": "Other"}] * 4},
                {"item_id": "C", "status": "EMPTY", "top5": []}]
        report = EVALUATE.score(rows, gold, ["A", "B", "C"])
        self.assertEqual(report["hits"], 1)
        self.assertEqual(report["operational_errors"], 2)
        self.assertTrue(report["outcomes"][0]["partial_candidates_not_scored"])
        self.assertIsNone(report["outcomes"][0]["target_rank"])
        self.assertEqual(report["outcomes"][1]["target_rank"], 1)


if __name__ == "__main__":
    unittest.main()
