"""A verified second URL cannot become a replacement sixth paper."""
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

import fitz
import pytest
import requests

from pipelines.run_manager import RunManager
from pipelines.state import Intent, Paper, PipelineState
from pipelines.tasks import prior_art_scoring as scoring
from pipelines.tasks import prior_art_utils as utils
from pipelines.tasks.prior_art_types import PriorArtConfig, PriorArtMatch


@pytest.fixture
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('No live requests in this test')
    for target in ('requests.sessions.Session.request', 'socket.create_connection', 'socket.socket.connect'):
        monkeypatch.setattr(target, blocked)
    monkeypatch.setattr(scoring.pdf_rate_limiter, 'wait_for_slot', lambda: None)
    root = Path(__file__).resolve().parents[1] / '.local/test_artifacts'
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as directory:
        manager = RunManager('fixture', base_dir=Path(directory))
        manager.create_run_directory()
        yield manager


def body():
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), '1 Introduction\nVerified same-paper evidence. ' * 12)
        return document.tobytes()


def alternative():
    return {'url': 'https://arxiv.org/pdf/2401.00001v2', 'arxiv_id': '2401.00001v2',
            'document_version': '2401.00001v2', 'identity_status': 'verified_arxiv_metadata',
            'evidence': {'exact_normalized_title': True, 'shared_publication_doi': ['10.1234/fixture0']}}


def state(count=1):
    return PipelineState(intent=Intent(task_type='prior_art_check', topic='fixture'), papers={
        f'P{i}': Paper(title=f'Fixture {i}', doi=f'10.1234/fixture{i}', year=2024,
                      arxiv_id='2401.00001', pdf_url=f'https://publisher.invalid/{i}.pdf')
        for i in range(count)})


def test_failed_publisher_then_verified_arxiv_preserves_identity_and_binding(offline, monkeypatch):
    manager, records, pdf = offline, [], body()
    def download(url, **kwargs):
        records.append(url)
        if 'publisher.invalid' in url:
            raise requests.HTTPError('403 Forbidden')
        return SimpleNamespace(content=pdf, raise_for_status=lambda: None)
    monkeypatch.setattr(scoring.requests, 'get', download)
    metadata_calls = []
    def metadata(query, limit, id_list):
        metadata_calls.append(id_list)
        return [Paper(title='Fixture 0', doi='10.1234/fixture0', arxiv_id='2401.00001v2',
                      document_version='2401.00001v2')]
    monkeypatch.setattr('pipelines.clients.scholarly.search_arxiv', metadata)
    current = state()
    scoring.enrich_papers_with_fulltext(current, ['P0'], max_workers=1, run_mgr=manager)
    paper = current.papers['P0']
    assert list(current.papers) == ['P0'] and paper.doi == '10.1234/fixture0'
    assert records == ['https://publisher.invalid/0.pdf', alternative()['url']]
    assert metadata_calls == [['2401.00001']]
    assert any(r['source'] == 'pdf_alternative_identity' and r['status'] == 'verified'
               for r in paper.source_records)
    assert paper.pdf_status == 'downloaded' and paper.chunks
    assert paper.pdf_source_binding['url'] == alternative()['url']
    assert paper.pdf_source_binding['arxiv_id'] == paper.document_version == '2401.00001v2'
    manifest = json.loads(next(manager.pdf_cache_dir.glob('*.source.json')).read_text())
    assert manifest['source_binding'] == paper.pdf_source_binding
    assert manifest['pdf_sha256'] == hashlib.sha256(pdf).hexdigest()
    assert [r['status'] for r in paper.source_records if r['source'] == 'pdf_download_attempt'] == ['FAILED', 'SUCCEEDED']
    assert not list(manager.pdf_cache_dir.glob('*.tmp.pdf'))


def test_failure_after_verified_alternate_never_replaces_fixed_five(offline, monkeypatch):
    manager, requests_seen, alternate_seen = offline, [], []
    def download(url, **kwargs):
        requests_seen.append(url)
        raise requests.HTTPError('403 Forbidden')
    def alternate(paper, failed_url):
        alternate_seen.append(paper.title)
        return [alternative()]
    monkeypatch.setattr(scoring.requests, 'get', download)
    monkeypatch.setattr(utils, 'verified_pdf_alternatives', alternate)
    current = state(6)
    matches = [PriorArtMatch(f'P{i}', coverage_level=3, task_similarity=.9, method_similarity=.8) for i in range(6)]
    ids = scoring.prepare_fixed_fulltext_for_eval(current, matches, list(current.papers), manager,
            PriorArtConfig(execution_mode='retrieval_eval', max_workers_pdf=1))
    assert ids == [f'P{i}' for i in range(5)]
    assert len(requests_seen) == 10 and len(alternate_seen) == 5
    assert 'https://publisher.invalid/5.pdf' not in requests_seen
    assert current.papers['P5'].pdf_status == 'not_attempted'
    plan = json.loads((manager.run_dir / 'eval_fulltext_plan.json').read_text())
    assert plan['attempted_count'] == 5 and plan['success_material_count'] == 0
    assert plan['replacement_policy'] == 'NO_REPLACEMENT'


def test_unverified_or_unavailable_alternate_does_not_download(offline, monkeypatch):
    calls = []
    def download(url, **kwargs):
        calls.append(url)
        raise requests.HTTPError('403')
    monkeypatch.setattr(scoring.requests, 'get', download)
    bad = alternative()
    bad['identity_status'] = 'unverified'
    monkeypatch.setattr(utils, 'verified_pdf_alternatives', lambda *args: [bad])
    current = state()
    scoring.enrich_papers_with_fulltext(current, ['P0'], max_workers=1, run_mgr=offline)
    assert len(calls) == 1 and current.papers['P0'].pdf_status == 'error'
