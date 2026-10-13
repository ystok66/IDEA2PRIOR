"""Saved-response format failures must not amplify into paid scoring retries."""
import json
from pathlib import Path
import tempfile

import pytest

from pipelines.runtime import LLMRuntime, RunLedger, use_runtime
from pipelines.state import Intent, Paper, PipelineState
from pipelines.tasks import prior_art_scoring as scoring
from pipelines.tasks.conservative_json import parse_json_locally
from pipelines.tasks.prior_art_types import PriorArtConfig


@pytest.fixture
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Offline test forbids network/model providers")
    for target in ('requests.sessions.Session.request', 'socket.create_connection', 'socket.socket.connect'):
        monkeypatch.setattr(target, blocked)
    monkeypatch.setattr(scoring.llm_rate_limiter, 'wait_for_slot', lambda: None)
    calls, answers = [], []

    class Model:
        def __init__(self, model_id, api_base, api_key):
            self.model_id, self.api_base, self.api_key = model_id, api_base, api_key
            self.kwargs = {}

        def __call__(self, messages, **kwargs):
            calls.append(messages)
            if not answers:
                raise AssertionError('Unexpected extra model request')
            return answers.pop(0)

    monkeypatch.setattr(scoring, 'LiteLLMModel', Model)
    monkeypatch.setattr(scoring, 'get_litellm_config', lambda **kwargs: ('fixture', 'offline://fixture', 'fixture'))
    root = Path(__file__).resolve().parents[1] / '.local/test_artifacts'
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as folder:
        runtime = LLMRuntime(RunLedger(Path(folder) / 'run'))
        with use_runtime(runtime):
            yield calls, answers, runtime


def score_payload():
    return {'coverage_level': 3, 'covered_facets': ['F1'], 'task_similarity': .9,
            'method_similarity': .8, 'notes': 'Matches the requested mechanism.',
            'high_level_facets': {key: ['prediction'] for key in scoring.HIGH_LEVEL_FACET_TYPES}}


def one_score(stage='initial', *, cfg=None):
    state = PipelineState(intent=Intent(task_type='prior_art_check', topic='prediction'), papers={
        'P': Paper(title='Fixture', abstract='Prediction evidence',
                   high_level_facets={key: [] for key in scoring.HIGH_LEVEL_FACET_TYPES})})
    config = cfg or PriorArtConfig(staged_retrieval=True, reuse_llm_across_stages=True, max_workers_llm_scoring=1)
    return scoring.score_papers_with_abstract_only(None, state, ['prediction'],
        {'datasets': set(), 'metrics': set(), 'method_families': set()}, {},
        idea_description='prediction idea', cfg=config, stage=stage)


def test_saved_ft005_facets_recover_six_without_second_call(offline):
    calls, answers, runtime = offline
    raw = ('["Speculative execution\n of programs", "Error recovery from incorrect guesses", '
           '"Safe state rollback mechanisms", "Asynchronous communication protocol verification", '
           '"Detection of premature side effects in shared state", "Dependency tracking for speculative results"]')
    answers.append(raw)
    assert scoring.decompose_idea_into_facets(None, 'original research idea') == [
        'Speculative execution of programs', 'Error recovery from incorrect guesses',
        'Safe state rollback mechanisms', 'Asynchronous communication protocol verification',
        'Detection of premature side effects in shared state', 'Dependency tracking for speculative results']
    assert len(calls) == 1
    task = runtime.ledger.task_statuses()[0]
    assert task['validation'] == 'VALID'
    raw_saved = json.loads((runtime.ledger.root / task['task_key'] / task['accepted_response']).read_text())
    assert raw_saved['response'] == raw
    assert 'LOCAL_JSON_FORMAT_RECOVERY' in (runtime.ledger.root / 'events.jsonl').read_text()


@pytest.mark.parametrize('raw', ['["truncated', '{"F1":"foo"}', '["only", "two"]',
                                  '["a", "a", "b"]', '["a", "b", 1]'])
def test_bad_facets_fail_before_fanout_without_line_split(offline, raw):
    calls, answers, runtime = offline
    answers.append(raw)
    with pytest.raises(ValueError, match='batch scoring was not started'):
        scoring.decompose_idea_into_facets(None, 'whole idea must not become fake facet')
    assert len(calls) == 1
    assert runtime.ledger.task_statuses()[0]['status'] == 'FAILED'


def test_recovered_merged_score_is_consumed_and_reused_without_fallback(offline):
    calls, answers, runtime = offline
    answers.append(json.dumps(score_payload()).replace('requested mechanism.', 'requested\n mechanism.'))
    first, second = one_score(), one_score('after_metadata')
    assert len(calls) == 1
    assert first[0].coverage_level == second[0].coverage_level == 3
    prompt = calls[0][0]['content']
    assert 'only labels from ["F1"]' in prompt
    assert '["F1", "F3"]' not in prompt
    assert any(t['validation'] == 'VALID' for t in runtime.ledger.task_statuses())


def test_invalid_labels_are_not_clipped_or_converted_to_zero(offline):
    calls, answers, runtime = offline
    bad = score_payload()
    bad['covered_facets'] = ['F3']
    answers.extend([json.dumps(bad), json.dumps(bad)])
    result = one_score()
    assert len(calls) == 2  # Existing merged->separate fallback; no added retries.
    assert result[0].coverage_level is None and result[0].scoring_status == 'failed'
    assert all(t['validation'] == 'INVALID' for t in runtime.ledger.task_statuses())


@pytest.mark.parametrize('raw', ['{"a": 1, "a": 2}', "{'a': 1, 'a': 2}",
                                  '{"n": NaN}', '{"n": 1e999}', "{'n': {1, 2}}",
                                  'Here is JSON: {"a":1}', '{"a":', '["a", "b"', '{"a":1} # comment'])
def test_parser_never_guesses_incomplete_or_ambiguous_content(raw):
    with pytest.raises(ValueError):
        parse_json_locally(raw)


def test_parser_preserves_strings_urls_and_legacy_literal_types():
    raw = '{"url":"https://example.org/a//b", "text":"comma,} and // stay"}'
    assert parse_json_locally(raw)[0] == json.loads(raw)
    assert parse_json_locally("{'a': ['legacy', None, True, 2.0]}")[0] == {'a': ['legacy', None, True, 2.0]}
    assert parse_json_locally('```json\n{"a":1}\n```')[0] == {'a': 1}
