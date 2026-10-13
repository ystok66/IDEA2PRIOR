"""Expert ranking and complete local PDF evidence; no external model or network."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import fitz

from pipelines.runtime import LLMRuntime, RunLedger, fingerprint, use_runtime
from pipelines.state import Chunk, Intent, Paper, PipelineState
from pipelines.tasks.prior_art_types import PriorArtMatch
from pipelines.tasks import prior_art_expert_ranking as expert


ROOT = Path(__file__).resolve().parents[1]


class FixtureModel:
    model_id = 'openai/fixture-expert'
    api_base = 'https://fixture.invalid'
    api_key = 'test-key-example-fixture-credential'
    kwargs = {}

    def __init__(self, owner):
        self.owner = owner

    def __call__(self, messages, **kwargs):
        self.owner.messages.append(copy.deepcopy(messages))
        if self.owner.model_error:
            raise self.owner.model_error
        return json.dumps(self.owner.response_payload)


class ExpertRankingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run_dir = self.root / 'run'
        self.run_dir.mkdir()
        self.manager = SimpleNamespace(run_dir=self.run_dir, pdf_pages_cache_dir=self.root / 'pages')
        self.manager.pdf_pages_cache_dir.mkdir()
        self.runtime = LLMRuntime(RunLedger(self.run_dir))
        self.state = PipelineState(intent=Intent(task_type='prior_art_check', topic='graph motion'), papers={
            name: Paper(paper_id=name, entity_id='entity-' + name, title='Paper ' + name,
                        abstract='Complete abstract ' + name, doi='10.1234/' + name.lower(), year=2020)
            for name in ('A', 'B', 'C', 'D', 'E', 'F', 'G')})
        self.matches = [PriorArtMatch(name, 3 if name == 'A' else 1, notes='Prior grade ' + name)
                        for name in self.state.papers]
        self.response_payload = {'candidates': [
            {'rank': rank, 'paper_id': name, 'expert_reason': 'Complete idea match ' + name,
             'evidence_source': 'abstract'}
            for rank, name in enumerate(('B', 'A', 'D', 'C', 'F'), 1)]}
        self.messages, self.model_error = [], None
        self.cfg = SimpleNamespace(retrieval_eval_pdf_text_mode='full_text',
                                   retrieval_eval_expert_compact_input=False)
        self.model = FixtureModel(self)
        config = patch.object(expert, 'get_litellm_config', return_value=(
            self.model.model_id, self.model.api_base, self.model.api_key))
        self.config = config.start()
        self.addCleanup(config.stop)
        model = patch.object(expert, 'LiteLLMModel', return_value=self.model)
        self.constructor = model.start()
        self.addCleanup(model.stop)
        for target in ('requests.sessions.Session.request', 'socket.socket.connect', 'socket.create_connection'):
            blocked = patch(target, side_effect=AssertionError('No live network or model calls'))
            blocked.start()
            self.addCleanup(blocked.stop)

    def run_rank(self, candidate_ids=None, attempted=None, years=(2015, 2025), cfg=None):
        with use_runtime(self.runtime):
            return expert.rerank_shortlist_with_expert(
                self.state, self.matches,
                list(self.state.papers) if candidate_ids is None else candidate_ids,
                ['A'] if attempted is None else attempted,
                'Recover latent relationships from object motion.', ['latent graph', 'dynamics'],
                self.manager, self.cfg if cfg is None else cfg, years=years)

    def read(self, name):
        return json.loads((self.run_dir / name).read_text(encoding='utf-8'))

    def pages(self, paper_id='A', texts=None):
        paper = self.state.papers[paper_id]
        raw = ('Offline verified local PDF bytes ' + paper_id).encode()
        path = self.root / (paper_id + '.pdf')
        path.write_bytes(raw)
        paper.local_pdf_path, paper.pdf_origin = str(path), 'explicit_local'
        paper.content_hash = hashlib.sha256(raw).hexdigest()
        paper.parser_version = expert.PARSER_VERSION
        paper.pdf_status, paper.pages_status, paper.has_fulltext = 'downloaded', 'parsed', True
        pages = [{'page': index + 1, 'text': text}
                 for index, text in enumerate(texts or ['First whole page', 'Last whole page'])]
        cache = self.manager.pdf_pages_cache_dir / f'{paper.content_hash}.{paper.parser_version}.pages.json'
        cache.write_text(json.dumps({'pdf_sha256': paper.content_hash,
            'parser_version': paper.parser_version, 'pages': pages, 'pages_sha256': fingerprint(pages)}),
            encoding='utf-8')
        return paper, cache, pages

    def test_expert_role_runtime_task_and_full_input_are_auditable(self):
        self.pages()
        self.response_payload['candidates'][1]['evidence_source'] = 'abstract_and_fulltext'
        result = self.run_rank()
        self.assertEqual(result['status'], 'SUCCEEDED')
        self.assertEqual(result['ranking_rule'], 'expert_shortlist_fulltext5_v1')
        self.config.assert_called_once_with(role='expert')
        self.constructor.assert_called_once_with(model_id=self.model.model_id,
            api_base=self.model.api_base, api_key=self.model.api_key, max_tokens=16384)
        saved = self.read('expert_ranking_input.json')
        sent = json.loads(self.messages[0][1]['content'])
        self.assertEqual(saved, sent)
        self.assertEqual(len(sent['candidates']), 7)
        a = next(row for row in sent['candidates'] if row['paper_id'] == 'A')
        self.assertEqual(a['abstract'], 'Complete abstract A')
        self.assertEqual(a['prior_assessment']['coverage_level'], 3)
        self.assertEqual(a['fulltext']['text'], 'First whole page\n\nLast whole page')
        self.assertTrue(a['fulltext']['complete'])
        self.assertEqual(a['fulltext']['source'], 'verified_pages_cache')
        self.assertEqual(result['input_sha256'], hashlib.sha256(
            (self.run_dir / 'expert_ranking_input.json').read_bytes()).hexdigest())
        tasks = self.runtime.ledger.task_statuses()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]['stage'], 'expert_ranking')
        self.assertEqual(tasks[0]['validation'], 'VALID')
        self.assertEqual([row['paper_id'] for row in result['candidates']], ['B', 'A', 'D', 'C', 'F'])
        self.assertEqual(result['candidates'][0]['coverage_level'], 1)
        self.assertEqual(result['candidates'][0]['abstract_match_explanation'], 'Prior grade B')
        self.assertTrue(all(row['identity_adjudication'] == 'PENDING' for row in result['candidates']))

    def test_legacy_openalex_candidate_keeps_stable_url(self):
        paper = self.state.papers['B']
        paper.doi = None
        paper.openalex_id = 'https://openalex.org/W123456'
        result = self.run_rank()
        self.assertEqual(result['status'], 'SUCCEEDED')
        self.assertEqual(result['candidates'][0]['url'], 'https://openalex.org/W123456')

    def test_compact_payload_keeps_evidence_and_exact_sent_payload_hashes(self):
        self.pages(texts=['Method latent graph details ' * 150, 'Conclusion object dynamics ' * 150])
        self.matches[0].abstract_notes = self.matches[0].notes
        result = self.run_rank(cfg=SimpleNamespace())
        self.assertEqual(result['status'], 'SUCCEEDED')
        audit = self.read('expert_ranking_input.json')
        payload = self.read('expert_ranking_model_payload.json')
        self.assertEqual(payload['messages'], self.messages[0])
        self.assertEqual(payload['request_parameters'], {'max_tokens': 16384})
        self.assertEqual(payload['input_audit_sha256'], result['input_sha256'])
        self.assertEqual(result['model_payload_sha256'], hashlib.sha256(
            (self.run_dir / 'expert_ranking_model_payload.json').read_bytes()).hexdigest())
        self.assertEqual(result['model_user_content_sha256'], hashlib.sha256(
            self.messages[0][1]['content'].encode('utf-8')).hexdigest())
        sent = json.loads(self.messages[0][1]['content'])
        self.assertEqual(len(sent['candidates']), len(audit['candidates']))
        for before, after in zip(audit['candidates'], sent['candidates']):
            for field in ('paper_id', 'entity_id', 'title', 'authors', 'year', 'venue', 'url',
                          'abstract', 'abstract_status', 'identity_status', 'field_conflicts'):
                self.assertEqual(after[field], before[field])
            for field in ('text', 'status', 'attempted', 'complete', 'representation',
                          'material_issues', 'validation_errors'):
                self.assertEqual(after['fulltext'][field], before['fulltext'][field])
            self.assertEqual(after['prior_assessment']['scoring_status'],
                             before['prior_assessment']['scoring_status'])
        self.assertIn('verification', audit['candidates'][0]['fulltext'])
        self.assertNotIn('verification', sent['candidates'][0]['fulltext'])
        self.assertNotIn('candidate_pool_ids', sent)
        self.assertNotIn('notes', sent['candidates'][0]['prior_assessment'])
        self.assertLess(result['model_input_characters'], result['audit_input_characters'])

    def test_compaction_preserves_different_notes_grades_and_unknown_fields(self):
        self.matches[0].abstract_notes = 'Different earlier explanation'
        self.matches[0].abstract_coverage_level = 1
        self.run_rank(cfg=SimpleNamespace())
        saved = self.read('expert_ranking_input.json')
        saved['future_field'] = {'do_not_drop': True}
        saved['requested_candidate_ids'].append('excluded-source-id')
        saved['candidates'][0]['fulltext']['future_evidence_warning'] = 'uncertain extraction'
        original = copy.deepcopy(saved)
        sent = expert._compact_model_input(saved)
        self.assertEqual(saved, original, 'The complete audit must remain unchanged')
        self.assertEqual(sent['future_field'], saved['future_field'])
        self.assertEqual(sent['requested_candidate_ids'], saved['requested_candidate_ids'])
        assessment = sent['candidates'][0]['prior_assessment']
        self.assertEqual(assessment['notes'], self.matches[0].notes)
        self.assertEqual(assessment['abstract_notes'], 'Different earlier explanation')
        self.assertEqual(assessment['coverage_level'], 3)
        self.assertEqual(assessment['abstract_coverage_level'], 1)
        self.assertEqual(sent['candidates'][0]['fulltext']['future_evidence_warning'], 'uncertain extraction')

    def test_expert_budget_is_configured_locally_and_recorded_in_request(self):
        from unittest.mock import Mock
        self.model._paperarena_set_output_budget = Mock()
        result = self.run_rank(cfg=SimpleNamespace(retrieval_eval_expert_max_output_tokens=24576))
        self.assertEqual(result['status'], 'SUCCEEDED')
        self.model._paperarena_set_output_budget.assert_called_once_with(role='expert', max_tokens=24576)
        self.assertEqual(self.constructor.call_args.kwargs['max_tokens'], 24576)
        self.assertEqual(self.read('expert_ranking_model_payload.json')['request_parameters'], {'max_tokens': 24576})
        self.assertEqual(result['expert_max_output_tokens'], 24576)

    def test_invalid_expert_policy_does_not_call_provider_or_construct_model(self):
        for overrides in ({'retrieval_eval_expert_compact_input': 'yes'},
                          {'retrieval_eval_expert_max_output_tokens': True},
                          {'retrieval_eval_expert_max_output_tokens': 1023},
                          {'retrieval_eval_expert_max_output_tokens': 65537}):
            with self.subTest(overrides=overrides):
                result = self.run_rank(cfg=SimpleNamespace(**overrides))
                self.assertEqual(result['status'], 'FAILED')
                self.assertEqual(result['error_type'], 'ValueError')
        self.config.assert_not_called()
        self.constructor.assert_not_called()

    def test_complete_control_character_json_parses_but_truncated_and_duplicate_keys_fail(self):
        self.run_rank()
        pool = {row['paper_id']: row for row in self.read('expert_ranking_input.json')['candidates']}
        payload = copy.deepcopy(self.response_payload)
        payload['candidates'][0]['expert_reason'] = 'First line\nsecond line'
        text = json.dumps(payload).replace('\\n', '\n')
        self.assertEqual(expert._validate_ranking(text, pool), payload)
        with self.assertRaises(ValueError):
            expert._validate_ranking(text[:-4], pool)
        with self.assertRaises(ValueError):
            expert._validate_ranking('{"candidates":[],"candidates":[]}', pool)

    def test_sixty_complete_abstracts_and_large_page_text_are_not_truncated(self):
        for number in range(7, 60):
            name = f'P{number}'
            self.state.papers[name] = Paper(title=name, paper_id=name,
                abstract=('Full abstract words ' * 800) + f' END-ABSTRACT-{number}', year=None)
        long_text = ('Full method and experiments text ' * 9000) + ' END-LAST-PAGE'
        self.state.papers['A'].abstract = ('Complete abstract ' * 1000) + ' END-ABSTRACT-A'
        self.pages(texts=['FIRST-PAGE', long_text])
        self.assertEqual(self.run_rank()['status'], 'SUCCEEDED')
        sent = json.loads(self.messages[0][1]['content'])
        self.assertEqual(sent['candidate_count'], 60)
        rows = {row['paper_id']: row for row in sent['candidates']}
        self.assertTrue(rows['A']['abstract'].endswith('END-ABSTRACT-A'))
        self.assertEqual(rows['A']['fulltext']['text'], 'FIRST-PAGE\n\n' + long_text)
        self.assertTrue(rows['P59']['abstract'].endswith('END-ABSTRACT-59'))
        self.assertEqual(rows['A']['fulltext']['pages'][1]['text_end'], len(rows['A']['fulltext']['text']))

    def test_default_snippets_have_one_text_and_exact_source_and_send_offsets(self):
        texts = [
            'Methods: latent graph relationships and dynamics ' + ('local filler ' * 300),
            'Experiments: object motion trajectories and latent graph predictions ' + ('measurement ' * 300),
            'Conclusion: dynamics recover graph relationships ' + ('conclusion detail ' * 300),
        ]
        _, _, pages = self.pages(texts=texts)
        self.response_payload['candidates'][1]['evidence_source'] = 'abstract_and_fulltext'
        result = self.run_rank(cfg=SimpleNamespace())
        self.assertEqual(result['status'], 'SUCCEEDED')
        self.assertEqual(result['pdf_text_policy'], {'mode': 'snippets', 'max_snippets': 6,
            'snippet_chars': 1800, 'selection_policy_version': 'idea_relevant_snippets_v1'})
        row = self.read('expert_ranking_input.json')['candidates'][0]
        material = row['fulltext']
        self.assertEqual(material['representation'], 'selected_snippets')
        self.assertFalse(material['complete'])
        self.assertLessEqual(len(material['snippets']), 6)
        self.assertLessEqual(len(material['text']), 6 * 1800 + 2 * 5)
        for snippet in material['snippets']:
            self.assertNotIn('text', snippet)
            source = pages[snippet['page'] - 1]['text'][snippet['source_start']:snippet['source_end']]
            sent = material['text'][snippet['sent_start']:snippet['sent_end']]
            self.assertEqual(source, sent)
            self.assertEqual(snippet['length'], len(source))
            self.assertLessEqual(len(source), 1800)
            self.assertEqual(snippet['text_sha256'], hashlib.sha256(source.encode()).hexdigest())
        self.assertEqual(material['selected_characters'], sum(s['length'] for s in material['snippets']))
        self.assertEqual(material['separator_characters'], 2 * (len(material['snippets']) - 1))
        chosen = next(candidate for candidate in result['candidates'] if candidate['paper_id'] == 'A')
        self.assertEqual(chosen['material_representation'], 'selected_snippets')
        self.assertEqual(chosen['evidence_scope'], 'complete_abstract_and_selected_pdf_snippets')
        self.assertFalse(chosen['fulltext_complete'])
        self.assertIn('unselected content is unknown', self.messages[0][0]['content'])
        self.assertIn('is not negative evidence', self.messages[0][0]['content'])

    def test_default_sixty_abstracts_are_complete_but_pdf_payload_is_bounded(self):
        for number in range(7, 60):
            name = f'P{number}'
            self.state.papers[name] = Paper(title=name, paper_id=name,
                abstract=('Full abstract words ' * 800) + f' END-ABSTRACT-{number}', year=None)
        long_text = ('method latent graph dynamics physical motion experiments ' * 9000) + ' END-PDF'
        self.pages(texts=[long_text])
        result = self.run_rank(cfg=SimpleNamespace())
        self.assertEqual(result['status'], 'SUCCEEDED')
        rows = {row['paper_id']: row for row in self.read('expert_ranking_input.json')['candidates']}
        self.assertEqual(len(rows), 60)
        self.assertTrue(rows['P59']['abstract'].endswith('END-ABSTRACT-59'))
        self.assertLessEqual(len(rows['A']['fulltext']['text']), 6 * 1800 + 10)
        self.assertGreater(len(long_text), len(rows['A']['fulltext']['text']) * 10)

    def test_snippet_selection_reaches_relevant_last_page_and_is_deterministic(self):
        texts = [
            'Unrelated background and literature ' * 50,
            'Unrelated statistics archive ' * 100,
            'Unrelated prior bibliography ' * 100,
            'Method: latent graph dynamics predict object motion relationships. LAST-PAGE-SIGNAL ' * 20,
        ]
        _, _, pages = self.pages(texts=texts)
        cfg = SimpleNamespace(retrieval_eval_pdf_max_snippets=2, retrieval_eval_pdf_snippet_chars=256)
        result = self.run_rank(cfg=cfg)
        self.assertEqual(result['status'], 'SUCCEEDED')
        first = self.read('expert_ranking_input.json')['candidates'][0]['fulltext']
        self.assertTrue(any(snippet['page'] == 4 for snippet in first['snippets']))
        self.assertIn('LAST-PAGE-SIGNAL', first['text'])
        self.assertLessEqual(len(first['text']), 2 * 256 + 2)
        self.runtime = LLMRuntime(RunLedger(self.run_dir / 'deterministic-repeat'))
        self.assertEqual(self.run_rank(cfg=cfg)['status'], 'SUCCEEDED')
        repeated = self.read('expert_ranking_input.json')['candidates'][0]['fulltext']
        self.assertEqual(first, repeated)

    def test_snippets_cover_relevant_method_experiment_and_conclusion_pages(self):
        self.pages(texts=[
            'Methods: latent graph dynamics ' * 30,
            'Experiments: latent graph dynamics ' * 30,
            'Conclusion: latent graph dynamics ' * 30])
        cfg = SimpleNamespace(retrieval_eval_pdf_max_snippets=3, retrieval_eval_pdf_snippet_chars=256)
        self.assertEqual(self.run_rank(cfg=cfg)['status'], 'SUCCEEDED')
        snippets = self.read('expert_ranking_input.json')['candidates'][0]['fulltext']['snippets']
        self.assertEqual({snippet['page'] for snippet in snippets}, {1, 2, 3})
        self.assertEqual({role for snippet in snippets for role in snippet['section_roles']},
                         {'method', 'experiment', 'conclusion'})

    def test_invalid_pdf_text_policy_fails_before_expert_or_native_extraction(self):
        self.pages()
        configurations = [
            SimpleNamespace(retrieval_eval_pdf_text_mode='unknown'),
            SimpleNamespace(retrieval_eval_pdf_max_snippets=0),
            SimpleNamespace(retrieval_eval_pdf_max_snippets=13),
            SimpleNamespace(retrieval_eval_pdf_max_snippets=True),
            SimpleNamespace(retrieval_eval_pdf_snippet_chars=255),
            SimpleNamespace(retrieval_eval_pdf_snippet_chars=4001),
        ]
        with patch.object(expert.PdfParserTool, '_extract_text_pymupdf',
                          side_effect=AssertionError('Invalid policy cannot extract pages')):
            for cfg in configurations:
                with self.subTest(cfg=vars(cfg)):
                    result = self.run_rank(cfg=cfg)
                    self.assertEqual(result['status'], 'FAILED')
                    self.assertEqual(result['error_type'], 'ValueError')
                    self.assertEqual(result['candidates'], [])
                    self.assertEqual(self.read('expert_ranking.json')['status'], 'FAILED')
        self.config.assert_not_called()
        self.constructor.assert_not_called()

    def test_missing_or_unattempted_fulltext_is_retained_and_may_rank_first(self):
        self.state.papers['A'].pdf_status = 'not_found'
        self.state.papers['B'].year = None
        result = self.run_rank()
        self.assertEqual(result['status'], 'SUCCEEDED')
        sent = self.read('expert_ranking_input.json')
        rows = {row['paper_id']: row for row in sent['candidates']}
        self.assertEqual(rows['A']['fulltext']['status'], 'UNAVAILABLE')
        self.assertEqual(rows['B']['fulltext']['status'], 'NOT_ATTEMPTED_FIXED_BUDGET')
        self.assertEqual(result['candidates'][0]['paper_id'], 'B')
        self.assertIn('not negative evidence', self.messages[0][0]['content'])
        self.assertEqual(len(sent['candidates']), 7)

    def test_scope_filters_conflicts_known_years_and_duplicate_entities_before_request(self):
        self.state.papers['A'].year = 2026
        self.state.papers['B'].identity_status = 'conflict'
        self.state.papers['D'].entity_id = self.state.papers['C'].entity_id
        self.state.papers['E'].year = None
        self.response_payload = {'candidates': [
            {'rank': rank, 'paper_id': name, 'expert_reason': 'Valid match', 'evidence_source': 'abstract'}
            for rank, name in enumerate(('C', 'E', 'F', 'G'), 1)]}
        result = self.run_rank(candidate_ids=['A', 'B', 'C', 'D', 'E', 'F', 'G', 'missing', 'C'])
        self.assertEqual(result['status'], 'SUCCEEDED')
        sent = self.read('expert_ranking_input.json')
        self.assertEqual(sent['candidate_pool_ids'], ['C', 'E', 'F', 'G'])
        self.assertEqual([row['reason'] for row in sent['excluded_candidates']], [
            'known_publication_year_out_of_scope', 'identity_conflict', 'duplicate_entity',
            'not_in_registry', 'duplicate_entity'])

    def test_invalid_expert_outputs_fail_without_rule_fallback(self):
        valid = copy.deepcopy(self.response_payload)
        mutations = []
        short = copy.deepcopy(valid); short['candidates'].pop(); mutations.append(short)
        duplicate = copy.deepcopy(valid); duplicate['candidates'][1]['paper_id'] = 'B'; mutations.append(duplicate)
        outsider = copy.deepcopy(valid); outsider['candidates'][0]['paper_id'] = 'OUTSIDE'; mutations.append(outsider)
        rank = copy.deepcopy(valid); rank['candidates'][0]['rank'] = 2; mutations.append(rank)
        rank_bool = copy.deepcopy(valid); rank_bool['candidates'][0]['rank'] = True; mutations.append(rank_bool)
        reason = copy.deepcopy(valid); reason['candidates'][0]['expert_reason'] = '  '; mutations.append(reason)
        source = copy.deepcopy(valid); source['candidates'][0]['evidence_source'] = 'fulltext_snippets'; mutations.append(source)
        fulltext = copy.deepcopy(valid); fulltext['candidates'][0]['evidence_source'] = 'abstract_and_fulltext'; mutations.append(fulltext)
        for payload in mutations:
            with self.subTest(payload=payload):
                self.response_payload = payload
                result = self.run_rank()
                self.assertEqual(result['status'], 'FAILED')
                self.assertEqual(result['error_type'], 'ValueError')
                self.assertEqual(result['candidates'], [])
                self.assertEqual(self.read('expert_ranking.json')['status'], 'FAILED')
        self.assertEqual(self.runtime.ledger.task_statuses()[0]['status'], 'FAILED')

    def test_small_pool_requires_exact_continuous_number_of_results(self):
        self.response_payload['candidates'] = [
            {'rank': 1, 'paper_id': 'B', 'expert_reason': 'Match B', 'evidence_source': 'abstract'},
            {'rank': 2, 'paper_id': 'A', 'expert_reason': 'Match A', 'evidence_source': 'abstract'}]
        result = self.run_rank(candidate_ids=['A', 'B'], attempted=[])
        self.assertEqual(result['status'], 'SUCCEEDED')
        self.assertEqual(len(result['candidates']), 2)

    def test_model_exception_and_invalid_json_are_failed_and_keep_input(self):
        self.model_error = RuntimeError('fixture context or model failure')
        result = self.run_rank()
        self.assertEqual(result['status'], 'FAILED')
        self.assertEqual(result['error_type'], 'RuntimeError')
        self.assertEqual(result['candidates'], [])
        self.assertTrue((self.run_dir / 'expert_ranking_input.json').is_file())
        self.assertEqual(self.runtime.ledger.task_statuses()[0]['status'], 'FAILED')
        self.model_error = None
        self.response_payload = 'invalid nonobject JSON'
        self.assertEqual(self.run_rank()['status'], 'FAILED')

    def test_empty_pool_saves_not_run_artifacts_without_model_or_config_calls(self):
        result = self.run_rank(candidate_ids=[], attempted=[])
        self.assertEqual(result['status'], 'NOT_RUN_EMPTY_POOL')
        self.assertEqual(result['candidates'], [])
        self.assertEqual(self.read('expert_ranking_input.json')['candidate_count'], 0)
        self.assertEqual(self.read('expert_ranking.json')['status'], 'NOT_RUN_EMPTY_POOL')
        self.config.assert_not_called()
        self.constructor.assert_not_called()
        self.assertEqual(self.runtime.ledger.task_statuses(), [])

    def test_mismatched_pdf_hash_or_source_binding_blocks_unverified_fulltext(self):
        paper, cache, _ = self.pages()
        paper.content_hash = '0' * 64
        with patch.object(expert.PdfParserTool, '_extract_text_pymupdf',
                          side_effect=AssertionError('Invalid bytes must not be recovered')):
            result = self.run_rank()
        self.assertEqual(result['status'], 'SUCCEEDED')
        a = self.read('expert_ranking_input.json')['candidates'][0]
        self.assertEqual(a['fulltext']['text'], '')
        self.assertIn('current_pdf_hash_mismatch', a['fulltext']['material_issues'])
        self.assertTrue(result['material_validation_errors'])
        paper, cache, _ = self.pages()
        paper.pdf_origin = 'remote_cache'
        paper.pdf_source_binding = {'paper_id': 'Different paper'}
        self.runtime = LLMRuntime(RunLedger(self.run_dir / 'binding-attempt'))
        with patch.object(expert.PdfParserTool, '_extract_text_pymupdf',
                          side_effect=AssertionError('Invalid source binding must not be recovered')):
            self.assertEqual(self.run_rank()['status'], 'SUCCEEDED')
        a = self.read('expert_ranking_input.json')['candidates'][0]
        self.assertIn('current_pdf_source_binding_mismatch', a['fulltext']['material_issues'])

    def test_damaged_page_cache_recovers_all_local_pages_without_network(self):
        paper, cache, _ = self.pages()
        body = json.loads(cache.read_text())
        body['pages_sha256'] = 'corrupted'
        cache.write_text(json.dumps(body))
        recovered = [{'page': 1, 'text': 'RECOVERED first page'},
                     {'page': 2, 'text': 'RECOVERED last page'}]
        with patch.object(expert.PdfParserTool, '_extract_text_pymupdf', return_value=recovered) as parse:
            result = self.run_rank()
        self.assertEqual(result['status'], 'SUCCEEDED')
        parse.assert_called_once_with(paper.local_pdf_path)
        fulltext = self.read('expert_ranking_input.json')['candidates'][0]['fulltext']
        self.assertEqual(fulltext['text'], 'RECOVERED first page\n\nRECOVERED last page')
        self.assertEqual(fulltext['source'], 'verified_local_pdf_native_recovery')
        self.assertTrue(fulltext['complete'])
        self.assertEqual(result['material_validation_errors'], [])

    def test_unrecoverable_pages_do_not_pass_partial_chunks_as_full_text(self):
        paper, cache, _ = self.pages()
        cache.unlink()
        paper.chunks = [Chunk(text='Only one partial method snippet')]
        with patch.object(expert.PdfParserTool, '_extract_text_pymupdf', side_effect=ValueError('fixture corrupt PDF')):
            self.assertEqual(self.run_rank()['status'], 'SUCCEEDED')
        fulltext = self.read('expert_ranking_input.json')['candidates'][0]['fulltext']
        self.assertEqual(fulltext['text'], '')
        self.assertFalse(fulltext['complete'])
        self.assertEqual(fulltext['available_chunk_count'], 1)
        self.assertTrue(any('native_recovery_failed' in issue for issue in fulltext['material_issues']))
        self.assertTrue(self.read('expert_ranking.json')['material_validation_errors'])

    def test_normal_not_found_has_no_material_validation_error(self):
        self.state.papers['A'].pdf_status = 'not_found'
        result = self.run_rank()
        self.assertEqual(result['status'], 'SUCCEEDED')
        self.assertEqual(result['material_validation_errors'], [])

    def test_downloaded_material_missing_hash_path_is_a_validation_error(self):
        self.state.papers['A'].pdf_status = 'downloaded'
        result = self.run_rank()
        self.assertEqual(result['status'], 'SUCCEEDED')
        self.assertEqual(result['material_validation_errors'][0]['paper_id'], 'A')

    def test_old_parser_cache_is_recovered_using_current_parser(self):
        paper, cache, pages = self.pages()
        paper.parser_version = 'old-parser-v1'
        old_path = cache.with_name(f'{paper.content_hash}.{paper.parser_version}.pages.json')
        old_path.write_text(json.dumps({'pdf_sha256': paper.content_hash,
            'parser_version': paper.parser_version, 'pages': pages,
            'pages_sha256': fingerprint(pages)}))
        new_pages = [{'page': 1, 'text': 'Current parser recovered complete page'}]
        with patch.object(expert.PdfParserTool, '_extract_text_pymupdf', return_value=new_pages) as parse:
            self.assertEqual(self.run_rank()['status'], 'SUCCEEDED')
        parse.assert_called_once_with(paper.local_pdf_path)
        fulltext = self.read('expert_ranking_input.json')['candidates'][0]['fulltext']
        self.assertEqual(fulltext['source'], 'verified_local_pdf_native_recovery')
        self.assertEqual(fulltext['verification']['parser_version'], expert.PARSER_VERSION)
        self.assertEqual(fulltext['text'], 'Current parser recovered complete page')

    def test_native_recovery_reads_first_and_last_page_of_real_local_pdf(self):
        path = self.root / 'native.pdf'
        with fitz.open() as document:
            document.new_page().insert_text((72, 72), 'NATIVE_FIRST_PAGE graph mechanism')
            document.new_page().insert_text((72, 72), 'NATIVE_LAST_PAGE experimental evidence')
            document.save(path)
        paper = self.state.papers['A']
        paper.local_pdf_path, paper.pdf_origin = str(path), 'explicit_local'
        paper.content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        paper.parser_version = expert.PARSER_VERSION
        paper.pdf_status, paper.has_fulltext = 'downloaded', True
        result = self.run_rank()
        self.assertEqual(result['status'], 'SUCCEEDED')
        fulltext = self.read('expert_ranking_input.json')['candidates'][0]['fulltext']
        self.assertEqual(fulltext['source'], 'verified_local_pdf_native_recovery')
        self.assertEqual(fulltext['page_count'], 2)
        self.assertIn('NATIVE_FIRST_PAGE', fulltext['text'])
        self.assertIn('NATIVE_LAST_PAGE', fulltext['text'])
        self.assertEqual(result['material_validation_errors'], [])

    def test_no_abstract_or_pdf_requires_metadata_only_evidence(self):
        self.state.papers['B'].abstract = None
        self.response_payload = {'candidates': [
            {'rank': 1, 'paper_id': 'B', 'expert_reason': 'Limited title and metadata inference',
             'evidence_source': 'metadata_only'}]}
        self.assertEqual(self.run_rank(candidate_ids=['B'], attempted=[])['status'], 'SUCCEEDED')
        self.assertEqual(self.read('expert_ranking.json')['candidates'][0]['evidence_source'], 'metadata_only')
        self.runtime = LLMRuntime(RunLedger(self.run_dir / 'invalid-abstract-attempt'))
        self.response_payload['candidates'][0]['evidence_source'] = 'abstract'
        self.assertEqual(self.run_rank(candidate_ids=['B'], attempted=[])['status'], 'FAILED')

    def test_pdf_without_abstract_requires_actual_fulltext_evidence(self):
        self.pages('B')
        self.state.papers['B'].abstract = None
        self.response_payload = {'candidates': [
            {'rank': 1, 'paper_id': 'B', 'expert_reason': 'Match from the supplied complete PDF text',
             'evidence_source': 'fulltext'}]}
        self.assertEqual(self.run_rank(candidate_ids=['B'], attempted=['B'])['status'], 'SUCCEEDED')

    def test_metadata_only_cannot_hide_supplied_abstract(self):
        self.response_payload['candidates'][0]['evidence_source'] = 'metadata_only'
        self.assertEqual(self.run_rank()['status'], 'FAILED')

    def test_nonattempted_candidate_cached_fulltext_is_not_added_to_fixed_budget(self):
        self.pages('B')
        with patch.object(expert.PdfParserTool, '_extract_text_pymupdf',
                          side_effect=AssertionError('No new material for nonattempted IDs')):
            self.assertEqual(self.run_rank(attempted=[])['status'], 'SUCCEEDED')
        sent = self.read('expert_ranking_input.json')
        b = next(row for row in sent['candidates'] if row['paper_id'] == 'B')
        self.assertEqual(b['fulltext']['status'], 'NOT_ATTEMPTED_FIXED_BUDGET')
        self.assertEqual(b['fulltext']['text'], '')

    def test_over_budget_pool_or_attempt_list_fails_without_silent_truncation(self):
        for number in range(7, 61):
            name = 'P' + str(number)
            self.state.papers[name] = Paper(title=name, paper_id=name, abstract='Whole abstract')
        result = self.run_rank(attempted=[])
        self.assertEqual(result['status'], 'FAILED')
        self.assertEqual(self.read('expert_ranking_input.json')['candidate_count'], 61)
        self.config.assert_not_called()
        self.constructor.assert_not_called()


if __name__ == '__main__':
    unittest.main()
