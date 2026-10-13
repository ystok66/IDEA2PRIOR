"""Crossref reference discovery contracts using synthetic offline evidence only."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from pipelines.clients import scholarly
from pipelines.identity import normalize_doi, upsert_paper
from pipelines.state import Intent, Paper, PipelineState
from pipelines.tasks import citation_neighbor_filter as filtering
from pipelines.tasks import crossref_reference_expansion as expansion
from pipelines.tasks import prior_art_retrieval as retrieval
from pipelines.tasks.candidate_selection import select_candidates
from pipelines.tasks.prior_art_types import PriorArtConfig, PriorArtMatch

REAL_VERIFY_PUBLICATION = scholarly.verify_publication


def s2_record(index, *, doi=None, title=None, abstract="Complete synthetic mechanism abstract."):
    return {"paperId": f"{index:040x}", "title": title or f"Synthetic reference {index}",
            "abstract": abstract, "year": 2021, "venue": "Synthetic Journal",
            "authors": [{"name": "Synthetic Author"}], "citationCount": 3,
            "externalIds": {"DOI": doi or f"10.1234/reference-{index}"}}


def crossref_record(doi, title, *, abstract="<jats:p>Complete synthetic abstract.</jats:p>"):
    row = {"DOI": doi, "title": [title], "type": "journal-article",
           "published": {"date-parts": [[2021]]}, "container-title": ["Synthetic Journal"]}
    if abstract is not None:
        row["abstract"] = abstract
    return row


def make_state():
    return PipelineState(intent=Intent(task_type="prior_art_check", topic="synthetic mechanism",
                                      years=(2015, 2025)))


def add_parent(state, name, references, *, s2_id=None):
    paper = Paper(title="Synthetic parent " + name, doi="10.1234/parent-" + name,
                  s2_id=s2_id, abstract="Complete parent abstract.", year=2021,
                  identity_status="verified_doi_title",
                  source_records=[{"source": "crossref", "metadata": {
                      "DOI": "10.1234/parent-" + name, "reference": deepcopy(references)}}])
    pid, _ = upsert_paper(state, paper, source="offline_fixture")
    return pid


class CrossrefReferenceExpansionTests(unittest.TestCase):
    def setUp(self):
        self.cfg = PriorArtConfig(staged_retrieval=True, retrieval_backend="s2_arxiv_crossref_v1",
                                  enable_openalex=False, citation_neighbor_filter="llm",
                                  enable_crossref_references=True,
                                  max_workers_s2=1, max_workers_llm_prefilter=1)
        self.batch_records = {}
        self.crossref_records = {}
        self.search_results = {}
        self.conflict_dois = set()
        self.model_payloads = []
        self.batch_inputs = []
        self.lookup_inputs = []
        self.search_inputs = []
        self.verify_inputs = []
        for target in ("requests.sessions.Session.request", "requests.get", "requests.post",
                       "socket.create_connection", "socket.socket.connect"):
            guard = patch(target, side_effect=AssertionError("Offline tests forbid live network"))
            guard.start()
            self.addCleanup(guard.stop)

        def metadata(identifiers, batch_size=100):
            self.batch_inputs.append(list(identifiers))
            result = []
            for raw in identifiers:
                key = scholarly.s2_lookup_id(raw)
                value = self.batch_records.get(key)
                if value is not None and "status" in value:
                    result.append({"input_id": key, "requested_id": raw, **deepcopy(value)})
                else:
                    result.append({"input_id": key, "requested_id": raw,
                                   "status": "SUCCEEDED" if value is not None else "NOT_FOUND",
                                   "data": deepcopy(value)})
            return result

        def lookup(doi=None, title=None):
            self.lookup_inputs.append((normalize_doi(doi), title))
            key = normalize_doi(doi) if doi else "title:" + str(title)
            return deepcopy(self.crossref_records.get(key))

        def search(query, years=None, limit=20):
            self.search_inputs.append((query, years, limit))
            return deepcopy(self.search_results.get(query, []))

        def verify(paper):
            self.verify_inputs.append(paper.doi)
            if normalize_doi(paper.doi) in self.conflict_dois:
                paper.identity_status = "conflict"
                paper.pdf_url = None
            else:
                paper.identity_status = "verified_doi_title"
                paper.publication_status = "published_metadata_verified"
            return paper

        for replacement in (
            patch.object(expansion.scholarly, "batch_metadata", side_effect=metadata),
            patch.object(expansion.scholarly, "crossref_lookup", side_effect=lookup),
            patch.object(expansion.scholarly, "search_s2", side_effect=search),
            patch.object(expansion.scholarly, "verify_publication", side_effect=verify),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)

    def put_s2(self, index, **kwargs):
        row = s2_record(index, **kwargs)
        self.batch_records[scholarly.s2_lookup_id(row["externalIds"]["DOI"])] = row
        return row

    def build(self, state):
        return expansion.build_crossref_reference_groups(state, self.cfg)

    def rows(self, groups):
        return [row for group in groups for row in group]

    def patch_semantic_model(self, *, score=2):
        def invoke(model, messages, **kwargs):
            payload = json.loads(next(message["content"] for message in reversed(messages)
                                      if message["role"] == "user"))
            self.model_payloads.append(payload)
            return json.dumps({"papers": [{"paper_id": row["paper_id"], "score": score,
                                           "reason": "Shared synthetic mechanism in the abstract."}
                                          for row in payload["papers"]]})

        replacements = (
            patch.object(filtering, "get_litellm_config", return_value=("offline-model", "offline://", "offline")),
            patch.object(filtering, "LiteLLMModel", return_value=object()),
            patch.object(filtering, "_invoke_with_run_reuse", side_effect=invoke),
        )
        for replacement in replacements:
            replacement.start()
            self.addCleanup(replacement.stop)

    def test_collection_deduplicates_doi_urls_case_and_retains_both_parents(self):
        state = make_state()
        first = add_parent(state, "one", [{"DOI": "https://doi.org/10.1234/SHARED"},
                                           {"DOI": "doi:10.1234/shared"}])
        second = add_parent(state, "two", [{"DOI": "HTTP://DX.DOI.ORG/10.1234/Shared"}])
        original = deepcopy(state.model_dump())
        items, audit = expansion.collect_crossref_references(state)
        self.assertEqual(len(items), 1)
        serialized = json.dumps(items, ensure_ascii=False)
        self.assertIn("10.1234/shared", serialized)
        self.assertIn(first, serialized)
        self.assertIn(second, serialized)
        self.assertEqual(state.model_dump(), original, "Discovery must not rewrite saved source evidence")
        self.assertTrue(audit)

    def test_unstructured_doi_is_resolved_but_unstructured_prose_is_not_a_title_query(self):
        state = make_state()
        add_parent(state, "one", [
            {"unstructured": "A. Author, Synthetic work. https://doi.org/10.1234/reference-1"},
            {"unstructured": "A. Author, 2020, some unstructured citation with no stable identifier."},
        ])
        self.put_s2(1)
        groups, sidecar, audit = self.build(state)
        self.assertEqual(len(self.rows(groups)), 1)
        self.assertEqual(len(sidecar), 1)
        self.assertEqual(sum(map(len, self.batch_inputs)), 1)
        self.assertEqual(self.search_inputs, [], "Do not guess a title from unstructured prose")
        self.assertFalse(any(title for _, title in self.lookup_inputs))
        self.assertTrue(audit)

    def test_crossref_discovery_does_not_require_an_s2_seed_and_keeps_complete_abstract(self):
        state = make_state()
        add_parent(state, "one", [{"DOI": "10.1234/reference-1"}])
        full_abstract = "Synthetic mechanism. " + "Evidence " * 700 + "ABSTRACT_TAIL"
        row = self.put_s2(1, abstract=full_abstract)
        original = deepcopy(state.model_dump())
        groups, sidecar, _ = self.build(state)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0][0]["abstract"], full_abstract)
        self.assertEqual(sidecar[filtering._key(row)].identity_status, "verified_s2_identifier")
        self.assertEqual(self.verify_inputs, [], "Exact DOI metadata identity already establishes the S2 match")
        self.assertEqual(state.model_dump(), original, "Only registration mutates the candidate state")

    def test_shared_reference_is_looked_up_and_verified_once_across_parent_groups(self):
        state = make_state()
        for name in ("one", "two", "three"):
            add_parent(state, name, [{"DOI": "10.1234/reference-1"}])
        self.put_s2(1)
        groups, sidecar, _ = self.build(state)
        self.assertEqual([len(group) for group in groups], [1, 1, 1])
        self.assertEqual(len(sidecar), 1)
        self.assertEqual(sum(map(len, self.batch_inputs)), 1)
        self.assertEqual(self.verify_inputs, [], "An exact S2 DOI match avoids a redundant Crossref request")
        self.patch_semantic_model()
        retained = filtering.filter_citation_neighbor_groups(groups, self.cfg, "Complete synthetic idea")
        self.assertEqual([len(group) for group in retained], [1, 1, 1])
        self.assertEqual(sum(len(payload["papers"]) for payload in self.model_payloads), 1)

    def test_publication_identity_conflict_is_rejected_before_semantic_scoring(self):
        state = make_state()
        add_parent(state, "one", [{"DOI": "10.1234/reference-1"}])
        self.put_s2(1, abstract=None)
        self.crossref_records["10.1234/reference-1"] = crossref_record(
            "10.1234/reference-1", "Synthetic reference 1")
        self.conflict_dois.add("10.1234/reference-1")
        groups, sidecar, audit = self.build(state)
        self.assertEqual(self.rows(groups), [])
        self.assertEqual(sidecar, {})
        self.assertIn("conflict", json.dumps(audit).lower())

    def test_s2_identity_conflict_does_not_become_an_accepted_crossref_fallback(self):
        state = make_state()
        add_parent(state, "one", [{"DOI": "10.1234/reference-1"}])
        self.batch_records["DOI:10.1234/reference-1"] = {
            "status": "IDENTITY_CONFLICT", "data": None}
        self.crossref_records["10.1234/reference-1"] = crossref_record(
            "10.1234/reference-1", "Synthetic reference 1")
        groups, sidecar, audit = self.build(state)
        self.assertEqual(self.rows(groups), [])
        self.assertEqual(sidecar, {})
        self.assertIn("conflict", json.dumps(audit).lower())

    def test_crossref_only_doi_reference_uses_real_identity_without_an_invented_s2_id(self):
        state = make_state()
        add_parent(state, "one", [{"DOI": "10.1234/only-crossref"}])
        self.crossref_records["10.1234/only-crossref"] = crossref_record(
            "10.1234/only-crossref", "Synthetic Crossref-only reference",
            abstract="<jats:p>Full abstract with <jats:italic>markup</jats:italic> and TAIL.</jats:p>")
        groups, sidecar, _ = self.build(state)
        row, = self.rows(groups)
        self.assertIsNone(row.get("paperId"))
        self.assertEqual(filtering._key(row), "DOI:10.1234/only-crossref")
        paper = sidecar[filtering._key(row)]
        self.assertIsNone(paper.s2_id)
        self.assertEqual(paper.doi, "10.1234/only-crossref")
        self.assertIn("Full abstract", row["abstract"])
        self.assertIn("TAIL.", row["abstract"])
        self.assertNotIn("<jats:", row["abstract"])
        original_ids = set(state.papers)
        self.patch_semantic_model()
        with patch.object(retrieval.SemanticScholarClient, "get_references",
                          side_effect=AssertionError("There is no selected S2 seed")), \
                patch.object(retrieval.SemanticScholarClient, "get_citations",
                             side_effect=AssertionError("There is no selected S2 seed")):
            retrieval.expand_corpus_via_citation_graph(
                state, [], self.cfg, ["synthetic mechanism"],
                idea_description="Complete synthetic idea", research_query="Original synthetic question")
        added = [paper for pid, paper in state.papers.items() if pid not in original_ids]
        self.assertEqual(len(added), 1, "Crossref reference registration works without selected S2 seeds")
        self.assertEqual(added[0].doi, "10.1234/only-crossref")
        self.assertIsNone(added[0].s2_id, "Typed registration must not invent an S2 ID")
        self.assertEqual(added[0].identity_status, "verified_doi_title")

        # A graph neighbor can establish an S2 alias for the same Crossref-only work.
        aligned_state = make_state()
        seed_id = add_parent(aligned_state, "aligned", [{"DOI": "10.1234/only-crossref"}],
                             s2_id="a" * 40)
        original_ids = set(aligned_state.papers)
        matching = s2_record(301, doi="10.1234/only-crossref",
                             title="Synthetic Crossref-only reference",
                             abstract="Complete graph abstract. " * 50)
        self.model_payloads.clear()
        with patch.object(retrieval.SemanticScholarClient, "get_references", return_value=[matching]), \
                patch.object(retrieval.SemanticScholarClient, "get_citations", return_value=[]):
            retrieval.expand_corpus_via_citation_graph(
                aligned_state, [PriorArtMatch(paper_id=seed_id, coverage_level=3)], self.cfg,
                ["synthetic mechanism"], max_workers=1, idea_description="Complete synthetic idea")
        judged = [row["paper_id"] for payload in self.model_payloads for row in payload["papers"]]
        self.assertEqual(judged, [matching["paperId"]], "Shared DOI/S2 identity is scored once")
        aligned = [paper for pid, paper in aligned_state.papers.items() if pid not in original_ids]
        self.assertEqual(len(aligned), 1)
        self.assertEqual(aligned[0].s2_id, matching["paperId"])
        self.assertFalse(scholarly._has_s2_metadata(aligned[0]),
                         "A partial graph record cannot suppress later complete metadata enrichment")

    def test_missing_abstract_is_retained_unknown_without_constructing_a_model(self):
        state = make_state()
        add_parent(state, "one", [{"DOI": "10.1234/reference-1"}])
        self.put_s2(1, abstract=None)
        groups, sidecar, _ = self.build(state)
        with patch.object(filtering, "LiteLLMModel", side_effect=AssertionError("Missing evidence cannot invoke a model")), \
                patch.object(filtering, "get_litellm_config", side_effect=AssertionError("No model credentials needed")):
            retained = filtering.filter_citation_neighbor_groups(groups, self.cfg, "Complete synthetic idea")
        row, = self.rows(retained)
        self.assertIsNone(row["citation_filter_assessment"]["score"])
        self.assertEqual(row["citation_filter_assessment"]["status"], "insufficient_evidence")
        self.assertIn(filtering._key(row), sidecar)

    def test_title_only_resolution_requires_exact_identity_then_deduplicates_strong_id(self):
        state = make_state()
        add_parent(state, "one", [{"article-title": "Synthetic reference 1"},
                                   {"DOI": "10.1234/reference-1"}])
        row = self.put_s2(1)
        self.search_results["Synthetic reference 1"] = [scholarly.paper_from_s2(row)]
        groups, sidecar, _ = self.build(state)
        self.assertEqual(len(self.rows(groups)), 1)
        self.assertEqual(len(sidecar), 1)
        self.assertEqual(len(self.verify_inputs), 0, "Exact S2 identity avoids a redundant Crossref verification")
        self.patch_semantic_model()
        filtering.filter_citation_neighbor_groups(groups, self.cfg, "Complete synthetic idea")
        self.assertEqual(sum(len(payload["papers"]) for payload in self.model_payloads), 1)

    def test_ambiguous_title_only_matches_are_unresolved_instead_of_first_hit(self):
        state = make_state()
        title = "Synthetic repeated title"
        add_parent(state, "one", [{"article-title": title}])
        self.search_results[title] = [
            scholarly.paper_from_s2(s2_record(1, title=title)),
            scholarly.paper_from_s2(s2_record(2, title=title)),
        ]
        groups, sidecar, audit = self.build(state)
        self.assertEqual(self.rows(groups), [])
        self.assertEqual(sidecar, {})
        self.assertEqual(self.verify_inputs, [])
        self.assertTrue(audit)

    def test_existing_strong_id_is_reused_without_lookup_verification_or_rescoring(self):
        state = make_state()
        add_parent(state, "one", [{"DOI": "https://doi.org/10.1234/EXISTING"}])
        current = Paper(title="Synthetic existing reference", doi="10.1234/existing",
                        s2_id="f" * 40, abstract="Already scored complete abstract.",
                        identity_status="verified_doi_title", year=2021)
        current_id, _ = upsert_paper(state, current, source="offline_existing")
        groups, _, audit = self.build(state)
        self.assertEqual(self.rows(groups), [], "Existing candidates do not need another neighbor judgment")
        self.assertEqual(self.batch_inputs, [])
        self.assertEqual(self.lookup_inputs, [])
        self.assertEqual(self.verify_inputs, [])
        self.assertEqual(state.papers[current_id].abstract, "Already scored complete abstract.")
        self.assertTrue(audit)

    def test_title_only_missing_abstract_rejects_crossref_fallback_with_conflicting_doi(self):
        state = make_state()
        title = "Synthetic same title with conflicting identities"
        add_parent(state, "one", [{"article-title": title}])
        self.search_results[title] = [scholarly.paper_from_s2(s2_record(
            1, doi="10.1234/original-identity", title=title, abstract=None))]
        self.crossref_records["title:" + title] = crossref_record(
            "10.1234/different-identity", title)
        groups, sidecar, audit = self.build(state)
        self.assertEqual(self.rows(groups), [])
        self.assertEqual(sidecar, {})
        self.assertEqual(audit["action_counts"].get("identity_conflict"), 1)
        self.assertEqual(self.model_payloads, [])

    def test_existing_s2_or_arxiv_alias_with_conflicting_doi_cannot_be_reused(self):
        for alias in ("s2", "arxiv"):
            with self.subTest(alias=alias):
                state = make_state()
                title = "Synthetic alias collision reference"
                add_parent(state, "one", [{"DOI": "10.1234/new-identity"}])
                existing = Paper(title=title, doi="10.1234/registered-identity",
                                 external_doi="10.1234/registered-identity",
                                 s2_id="1" * 40 if alias == "s2" else None,
                                 arxiv_id="2101.12345" if alias == "arxiv" else None,
                                 abstract="Already registered evidence.",
                                 identity_status="verified_doi_title")
                existing_id, _ = upsert_paper(state, existing, source="offline_existing")
                before = deepcopy(state.papers[existing_id].model_dump())
                row = self.put_s2(1, doi="10.1234/new-identity", title=title)
                if alias == "s2":
                    row["paperId"] = "1" * 40
                else:
                    row["externalIds"]["ArXiv"] = "2101.12345"
                groups, sidecar, audit = self.build(state)
                self.assertEqual(self.rows(groups), [])
                self.assertEqual(sidecar, {})
                self.assertEqual(audit["action_counts"].get("existing_identity_conflict"), 1)
                self.assertEqual(audit["action_counts"].get("already_registered", 0), 0)
                self.assertEqual(state.papers[existing_id].model_dump(), before)

    def test_crossref_inline_title_markup_passes_real_publication_verification(self):
        state = make_state()
        doi = "10.1234/inline-title"
        add_parent(state, "one", [{"DOI": doi}])
        self.crossref_records[doi] = crossref_record(doi, "A<sub>1</sub> mechanism")
        # The real verifier uses the mocked Crossref source; no identity rules are mocked here.
        with patch.object(expansion.scholarly, "verify_publication", side_effect=REAL_VERIFY_PUBLICATION):
            groups, sidecar, audit = self.build(state)
        row, = self.rows(groups)
        paper = sidecar[filtering._key(row)]
        self.assertEqual(paper.title, "A1 mechanism")
        self.assertEqual(paper.identity_status, "verified_doi_title")
        self.assertEqual(paper.publication_status, "published_metadata_verified")
        self.assertEqual(paper.publication_year_source, "crossref")
        self.assertEqual(paper.year, 2021)
        self.assertNotIn("crossref_title", paper.field_conflicts)
        self.assertEqual(audit["action_counts"].get("resolved"), 1)

    def test_crossref_and_s2_same_doi_different_titles_are_isolated_before_model_scoring(self):
        state = make_state()
        doi = "10.1234/provider-title-conflict"
        parent_id = add_parent(state, "one", [{"DOI": doi}], s2_id="a" * 40)
        original_ids = set(state.papers)
        self.crossref_records[doi] = crossref_record(doi, "Synthetic authoritative title")
        wrong = s2_record(201, doi=doi, title="Entirely different synthetic paper")
        control = s2_record(202, title="Synthetic unaffected control reference")
        self.patch_semantic_model()
        with patch.object(retrieval.SemanticScholarClient, "get_references", return_value=[wrong, control]), \
                patch.object(retrieval.SemanticScholarClient, "get_citations", return_value=[]):
            retrieval.expand_corpus_via_citation_graph(
                state, [PriorArtMatch(paper_id=parent_id, coverage_level=3)], self.cfg,
                ["synthetic mechanism"], max_workers=1,
                idea_description="Complete synthetic idea", research_query="Original synthetic question")
        judged = [row for payload in self.model_payloads for row in payload["papers"]]
        self.assertEqual([row["paper_id"] for row in judged], [control["paperId"]])
        added = [paper for pid, paper in state.papers.items() if pid not in original_ids]
        self.assertEqual([paper.s2_id for paper in added], [control["paperId"]])
        self.assertFalse(any(paper.doi == doi for paper in added))

    def test_alignment_tracks_new_s2_alias_and_isolates_later_conflicting_doi(self):
        state = make_state()
        doi = "10.1234/dynamic-alias"
        title = "Synthetic dynamic alias reference"
        add_parent(state, "one", [{"DOI": doi}])
        self.crossref_records[doi] = crossref_record(doi, title)
        groups, sidecar, _ = self.build(state)
        initial, = self.rows(groups)
        self.assertIsNone(initial.get("paperId"))
        first = s2_record(401, doi=doi, title=title)
        conflicting = s2_record(401, doi="10.1234/different-later-doi", title=title)
        graph_groups, reference_groups, resolved, conflicts = expansion.align_crossref_neighbor_groups(
            [[first], [conflicting]], groups, sidecar)
        self.assertEqual(self.rows(graph_groups), [], "Quarantine earlier and later graph occurrences")
        self.assertEqual(self.rows(reference_groups), [])
        self.assertEqual(resolved, {})
        self.assertTrue(conflicts)
        self.assertEqual(self.model_payloads, [], "Identity reconciliation runs before model scoring")

    def test_integrated_s2_and_crossref_discovery_share_semantic_gate_and_global_50_budget(self):
        state = make_state()
        parents = []
        for parent_index in range(3):
            references = [{"DOI": f"10.1234/reference-{index}"}
                          for index in range(1 + parent_index * 20, 21 + parent_index * 20)]
            parents.append(add_parent(state, str(parent_index), references,
                                      s2_id="a" * 40 if parent_index == 0 else None))
            for index in range(1 + parent_index * 20, 21 + parent_index * 20):
                self.put_s2(index)
        original_ids = set(state.papers)
        graph_rows = [s2_record(index) for index in range(101, 121)]
        self.patch_semantic_model()
        with patch.object(retrieval.SemanticScholarClient, "get_references", return_value=graph_rows), \
                patch.object(retrieval.SemanticScholarClient, "get_citations", return_value=[]):
            retrieval.expand_corpus_via_citation_graph(
                state, [PriorArtMatch(paper_id=parents[0], coverage_level=3)], self.cfg,
                ["synthetic", "mechanism"], max_workers=1,
                idea_description="Complete synthetic research idea", facets=["Synthetic mechanism"],
                research_query="Original synthetic user question", search_queries=["Synthetic search query"])
        added = {pid: paper for pid, paper in state.papers.items() if pid not in original_ids}
        self.assertEqual(len(added), 60, "One S2 group and three Crossref groups each retain 15 positives")
        judged = [row["paper_id"] for payload in self.model_payloads for row in payload["papers"]]
        self.assertEqual(len(judged), 80)
        self.assertEqual(len(set(judged)), 80)
        self.assertTrue(all(payload["research_query"] == "Original synthetic user question"
                            for payload in self.model_payloads))
        selected, audit = select_candidates(added, self.cfg, "Complete synthetic research idea",
                                            limit=self.cfg.citation_new_candidate_limit, years=(2015, 2025))
        self.assertEqual(len(selected), 50, "The existing global budget counts both providers together")
        self.assertEqual(audit["budget"], 50)
        crossref_dois = {f"10.1234/reference-{index}" for index in range(1, 61)}
        self.assertTrue(any(paper.doi in crossref_dois for paper in added.values()))
        self.assertTrue(any(paper.doi not in crossref_dois for paper in added.values()))


if __name__ == "__main__":
    unittest.main()
