"""Typed scholarly retrieval without an OpenAlex dependency.

Metadata caches belong to one runtime/material snapshot. Search and citation
requests are not model draws and never replace independent LLM assessments.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from html import unescape
from html.parser import HTMLParser
from urllib.parse import quote, unquote
import xml.etree.ElementTree as ET
import copy
import threading
from contextlib import contextmanager, ExitStack

from loguru import logger
from pipelines.clients.http_transport import get, post
from pipelines.clients.retrieval_retry import call_readonly
from pipelines.clients.doi_metadata import arxiv_doi_id, datacite_lookup, doi_agency
from pipelines.runtime import get_runtime
from pipelines.state import Paper

FIELDS = 'paperId,title,abstract,authors,year,venue,publicationDate,citationCount,referenceCount,externalIds,openAccessPdf,url,tldr,s2FieldsOfStudy'
ATOM = '{http://www.w3.org/2005/Atom}'
ARXIV = '{http://arxiv.org/schemas/atom}'
_CACHE_INITIALIZATION_LOCK = threading.Lock()


def _optimized_requests(cfg=None):
    policy = getattr(get_runtime(), 'retrieval_policy', {})
    return bool(getattr(cfg, 'optimize_scholarly_requests',
                        policy.get('optimize_scholarly_requests', False)))


def _run_cache(name):
    """Caches and locks have exactly the lifetime of the active runtime."""
    runtime = get_runtime()
    if runtime is None:
        return {}, {}
    with _CACHE_INITIALIZATION_LOCK:
        if not hasattr(runtime, name):
            setattr(runtime, name, {})
        lock_name = name + '_locks'
        if not hasattr(runtime, lock_name):
            setattr(runtime, lock_name, {})
        return getattr(runtime, name), getattr(runtime, lock_name)


@contextmanager
def _cache_reads(name, keys):
    """Coalesce overlapping reads; sorted acquisition avoids batch deadlocks."""
    cache, locks = _run_cache(name)
    if not _optimized_requests():
        yield cache
        return
    with _CACHE_INITIALIZATION_LOCK:
        requested = [locks.setdefault(key, threading.RLock())
                     for key in sorted(set(keys), key=repr)]
    with ExitStack() as stack:
        for lock in requested:
            stack.enter_context(lock)
        yield cache


def _s2_scope():
    # Keep credential identity out of logs and cache keys as plaintext.
    headers = _headers()
    identity = hashlib.sha256(json.dumps(headers, sort_keys=True).encode()).hexdigest()
    return _base(), identity


def _s2_metadata_key(identifier):
    return (*_s2_scope(), identifier, FIELDS)


def replacement_enabled(cfg=None):
    if cfg is not None:
        return getattr(cfg, 'retrieval_backend', 'legacy_openalex') == 's2_arxiv_crossref_v1'
    return getattr(get_runtime(), 'retrieval_policy', {}).get('retrieval_backend') == 's2_arxiv_crossref_v1'


def staged_retrieval_enabled(cfg=None):
    if cfg is not None:
        return bool(getattr(cfg, 'staged_retrieval', False))
    return bool(getattr(get_runtime(), 'retrieval_policy', {}).get('staged_retrieval', False))


def outcome(stage, status, *, quiet=False, **details):
    runtime = get_runtime()
    if runtime:
        runtime.ledger.event('RETRIEVAL_RESULT', stage=stage, status=status, **details)
        if status in ('FAILED', 'INVALID_RESPONSE', 'IDENTITY_CONFLICT'):
            if not hasattr(runtime, 'retrieval_issues'):
                runtime.retrieval_issues = []
            runtime.retrieval_issues.append({'stage': stage, 'status': status, **details})
    if not quiet and status in ('FAILED', 'INVALID_RESPONSE', 'IDENTITY_CONFLICT'):
        logger.warning(f'[Retrieval] {stage}: {status}')


def s2_id(value):
    """JSON IDs, not escaped URL paths. OpenAlex W IDs are not S2 IDs."""
    value = unquote(str(value or '')).strip()
    value = re.sub(r'^https?://(?:dx\.)?doi.org/', 'DOI:', value, flags=re.I)
    value = re.sub(r'^https?://(?:(?:export|www)\.)?arxiv.org/(?:abs|pdf)/', 'ARXIV:', value, flags=re.I)
    if value.upper().startswith('ARXIV:'):
        value = re.sub(r'\.pdf$', '', value, flags=re.I)
    if re.match(r'^10\.\d{4,9}/\S+$', value):
        value = 'DOI:' + value
    if re.match(r'^\d{4}\.\d{4,5}(v\d+)?$', value) or re.match(r'^[a-z.-]+/\d{7}(v\d+)?$', value, re.I):
        value = 'ARXIV:' + value
    if re.fullmatch(r'[a-fA-F0-9]{40}', value):
        return value.lower()
    if re.match(r'^DOI:10\.\d{4,9}/\S+$', value, re.I):
        return 'DOI:' + value[4:].lower()
    if value.upper().startswith('ARXIV:') and re.fullmatch(r'(?:\d{4}\.\d{4,5}|[a-z.-]+/\d{7})(?:v\d+)?', value[6:], re.I):
        return 'ARXIV:' + value[6:]
    return None


def paper_lookup_id(paper):
    for name in ('s2_id', 'doi', 'arxiv_id', 'url'):
        value = paper.get(name) if isinstance(paper, dict) else getattr(paper, name, None)
        if s2_lookup_id(value):
            return s2_lookup_id(value)
    return None


def s2_lookup_id(value):
    """Work metadata key only; never change the source/PDF version identifier."""
    identifier = s2_id(value)
    if identifier and identifier.startswith('ARXIV:'):
        return re.sub(r'v\d+$', '', identifier, flags=re.I)
    if identifier and identifier.startswith('DOI:'):
        aid = arxiv_doi_id(identifier[4:])
        if aid:
            return 'ARXIV:' + aid
    return identifier


def _headers():
    key = os.getenv('Semantic_Scholar_API_key')
    return {'x-api-key': key} if key else {}


def _base():
    return os.getenv('Semantic_Scholar_URL', 'https://api.semanticscholar.org/graph/v1').rstrip('/')


def _matches_id(identifier, data):
    external = data.get('externalIds') or {}
    if identifier.startswith('DOI:'):
        return s2_id(external.get('DOI')) == identifier
    if identifier.startswith('ARXIV:'):
        returned = s2_lookup_id(external.get('ArXiv')) or s2_lookup_id(external.get('DOI'))
        return returned is not None and re.sub(r'v\d+$', '', returned) == re.sub(r'v\d+$', '', identifier)
    return data.get('paperId') == identifier


def batch_metadata(identifiers, batch_size=100):
    if type(batch_size) is not int or not 1 <= batch_size <= 500:
        raise ValueError('S2 batch size must be between 1 and 500')
    identifiers = list(identifiers)
    normalized = [s2_lookup_id(x) for x in identifiers]
    unique = list(dict.fromkeys(x for x in normalized if x))
    with _cache_reads('s2_metadata_cache', [_s2_metadata_key(key) for key in unique]) as cache:
        return _batch_metadata_locked(identifiers, normalized, unique, batch_size, cache)


def _batch_metadata_locked(identifiers, normalized, unique, batch_size, cache):
    results = {}
    pending = []
    for key in unique:
        cache_key = _s2_metadata_key(key)
        if cache_key in cache:
            results[key] = cache[cache_key]
            outcome('s2_batch_item', results[key]['status'], input_id=key, cache_hit=True)
        else:
            pending.append(key)

    def fetch(keys):
        response = None
        try:
            response = call_readonly(post, _base() + '/paper/batch', method='POST', read_only_batch=True, params={'fields': FIELDS},
                            json={'ids': keys}, headers=_headers(), timeout=(10, 60))
            if response.status_code == 413 and len(keys) > 1:
                midpoint = len(keys) // 2
                fetch(keys[:midpoint])
                fetch(keys[midpoint:])
                return
            response.raise_for_status()
            rows = response.json()
            if not isinstance(rows, list) or len(rows) != len(keys):
                raise ValueError('Batch response length/type does not match requested slots')
            for key, data in zip(keys, rows):
                if data is None:
                    status = 'NOT_FOUND'
                elif not isinstance(data, dict) or not data.get('paperId'):
                    status = 'INVALID_RESPONSE'
                elif not _matches_id(key, data):
                    status = 'IDENTITY_CONFLICT'
                else:
                    status = 'SUCCEEDED'
                results[key] = {'input_id': key, 'status': status, 'data': data if status == 'SUCCEEDED' else None}
                outcome('s2_batch_item', status, input_id=key)
                if status == 'NOT_FOUND':
                    cache[_s2_metadata_key(key)] = copy.deepcopy(results[key])
                if status == 'SUCCEEDED':
                    cache[_s2_metadata_key(key)] = copy.deepcopy(results[key])
                    for alias in [data.get('paperId'), (data.get('externalIds') or {}).get('DOI'), (data.get('externalIds') or {}).get('ArXiv')]:
                        if s2_lookup_id(alias) and _matches_id(s2_lookup_id(alias), data):
                            cache[_s2_metadata_key(s2_lookup_id(alias))] = copy.deepcopy(results[key])
        except Exception as exc:
            http_status = response.status_code if response is not None else None
            logger.warning('[Retrieval] S2 batch failed: {} IDs, HTTP {}, {}', len(keys), http_status, type(exc).__name__)
            for key in keys:
                results[key] = {'input_id': key, 'status': 'FAILED', 'data': None, 'error_type': type(exc).__name__}
                outcome('s2_batch_item', 'FAILED', quiet=True, input_id=key, error_type=type(exc).__name__, http_status=http_status)

    for start in range(0, len(pending), batch_size):
        fetch(pending[start:start + batch_size])
    return [dict(copy.deepcopy(results[key]), input_id=key, requested_id=raw) if key else {'input_id': raw, 'status': 'INVALID_ID', 'data': None}
            for raw, key in zip(identifiers, normalized)]


def paper_from_s2(data, source='s2_search'):
    external = data.get('externalIds') or {}
    paper = Paper(title=data.get('title') or 'Unknown Title', authors=[a.get('name', '') for a in data.get('authors') or []],
                 abstract=data.get('abstract'), year=data.get('year'), venue=data.get('venue'),
                 s2_id=data.get('paperId'), doi=external.get('DOI'), arxiv_id=external.get('ArXiv'),
                 citation_count=data.get('citationCount'), citation_count_source='semantic_scholar',
                 pdf_url=(data.get('openAccessPdf') or {}).get('url'), retrieval_source=source,
                 source_records=[{'source': source, 'metadata': data}],
                 fields_of_study=[x['category'] for x in data.get('s2FieldsOfStudy') or [] if x.get('category')],
                 publication_date=data.get('publicationDate'), publication_year_source='semantic_scholar')
    assign_doi(paper, paper.doi)
    return paper


def assign_doi(paper, value):
    """Retain preprint and external publication identifiers independently."""
    identifier = s2_id(value)
    if not identifier or not identifier.startswith('DOI:'):
        return
    doi = identifier[4:]
    aid = arxiv_doi_id(doi)
    field = 'preprint_doi' if aid else 'external_doi'
    old = getattr(paper, field)
    if old and old != doi:
        paper.field_conflicts[field] = [old, doi]
        paper.identity_status, paper.pdf_url = 'conflict', None
        outcome('doi_identity', 'IDENTITY_CONFLICT', input_id=doi)
        return
    setattr(paper, field, doi)
    if aid:
        if paper.arxiv_id and s2_lookup_id(paper.arxiv_id) != 'ARXIV:' + aid:
            paper.identity_status, paper.pdf_url = 'conflict', None
            paper.field_conflicts['arxiv_doi'] = [paper.arxiv_id, doi]
            outcome('doi_identity', 'IDENTITY_CONFLICT', input_id=doi)
        elif not paper.arxiv_id:
            paper.arxiv_id = aid
    paper.doi = paper.publication_doi or paper.external_doi or paper.preprint_doi


def plain_query(query):
    # S2 relevance search has no OpenAlex/arXiv boolean query syntax.
    return re.sub(r'\s+', ' ', re.sub(r'\b(?:AND|OR|NOT)\b|["()]|-', ' ', query)).strip()


def search_s2(query, years=None, limit=20):
    params = {'query': plain_query(query), 'limit': min(limit, 100), 'fields': FIELDS}
    if years:
        params['year'] = f'{years[0]}-{years[1]}'
    key = (*_s2_scope(), '/paper/search', json.dumps(params, sort_keys=True))
    if not _optimized_requests():
        return _search_s2_locked(query, params, key, {})
    with _cache_reads('s2_search_cache', [key]) as cache:
        return _search_s2_locked(query, params, key, cache)


def _search_s2_locked(query, params, key, cache):
    try:
        cache_hit = key in cache
        if cache_hit:
            body = copy.deepcopy(cache[key])
        else:
            response = call_readonly(get, _base() + '/paper/search', params=params, headers=_headers())
            response.raise_for_status()
            body = response.json()
            # A valid zero-result response may omit data. Other absent/null
            # arrays and malformed paper records must remain retryable failures.
            if (isinstance(body, dict) and 'data' not in body
                    and type(body.get('total')) is int and body['total'] == 0):
                body = {**body, 'data': []}
            if not isinstance(body, dict) or not isinstance(body.get('data'), list):
                raise ValueError('Missing S2 search data array')
            if any(not isinstance(row, dict) or not row.get('paperId') for row in body['data']):
                raise ValueError('Malformed S2 search paper record')
        papers = [paper_from_s2(copy.deepcopy(row)) for row in body['data']]
        if not cache_hit:
            # Materialize every record before caching: one invalid row cannot
            # turn a failed search into a cache hit on the next consumer.
            metadata_keys = []
            metadata_rows = []
            for data in body['data']:
                aliases = [data.get('paperId'), (data.get('externalIds') or {}).get('DOI'), (data.get('externalIds') or {}).get('ArXiv')]
                for alias in aliases:
                    identifier = s2_lookup_id(alias)
                    if identifier and _matches_id(identifier, data):
                        metadata_key = _s2_metadata_key(identifier)
                        metadata_keys.append(metadata_key)
                        metadata_rows.append((metadata_key, {'input_id': identifier, 'status': 'SUCCEEDED', 'data': data}))
            with _cache_reads('s2_metadata_cache', metadata_keys) as metadata_cache:
                for metadata_key, value in metadata_rows:
                    metadata_cache[metadata_key] = copy.deepcopy(value)
            cache[key] = copy.deepcopy(body)
        outcome('s2_search', 'SUCCEEDED', query=query, effective_query=params['query'],
                count=len(papers), cache_hit=cache_hit)
        return papers
    except Exception as exc:
        outcome('s2_search', 'FAILED', query=query, error_type=type(exc).__name__)
        return []


def arxiv_query(query):
    phrases = re.findall(r'"([^"]+)"', query)
    remainder = re.sub(r'"[^"]+"', ' ', query)
    terms = [p.strip() for p in phrases if p.strip()] + re.findall(r'[\w]+', plain_query(remainder))
    return ' AND '.join('all:"' + term.replace('"', '') + '"' for term in terms)


def search_arxiv(query, limit=20, id_list=None):
    runtime = get_runtime()
    if not id_list and not getattr(runtime, 'retrieval_policy', {}).get('enable_arxiv_search', False):
        outcome('arxiv_search', 'SKIPPED', quiet=True, reason='keyword_title_search_disabled')
        return []
    params = {'start': 0, 'max_results': limit, 'sortBy': 'relevance'}
    if id_list:
        params['id_list'] = ','.join(id_list)
    else:
        params['search_query'] = arxiv_query(query)
    cache_key = ('https://export.arxiv.org/api/query', json.dumps(params, sort_keys=True))
    with _cache_reads('arxiv_cache', [cache_key]) as cache:
        return _search_arxiv_locked(query, params, cache_key, cache)


def _search_arxiv_locked(query, params, cache_key, cache):
    if cache_key in cache:
        papers = copy.deepcopy(cache[cache_key])
        outcome('arxiv_search', 'SUCCEEDED', query=query,
                effective_query=params.get('search_query'), count=len(papers), cache_hit=True)
        return papers
    try:
        response = get('https://export.arxiv.org/api/query', params=params, response_format='atom')
        response.raise_for_status()
        feed = ET.fromstring(response.text)
        papers = []
        for entry in feed.findall(ATOM + 'entry'):
            identifier = entry.findtext(ATOM + 'id', '')
            if '/api/errors' in identifier:
                raise ValueError('arXiv returned an API error entry')
            aid = s2_id(identifier)
            if not aid:
                raise ValueError('arXiv entry has no valid identifier')
            full_id = aid[6:]
            doi = entry.findtext(ARXIV + 'doi')
            pdf = next((e.get('href') for e in entry.findall(ATOM + 'link') if e.get('title') == 'pdf' or e.get('type') == 'application/pdf'), None)
            paper = Paper(title=' '.join(entry.findtext(ATOM + 'title', '').split()),
                abstract=' '.join(entry.findtext(ATOM + 'summary', '').split()),
                authors=[a.findtext(ATOM + 'name', '') for a in entry.findall(ATOM + 'author')],
                year=None, venue=None, doi=doi, arxiv_id=full_id, document_version=full_id,
                preprint_published=entry.findtext(ATOM + 'published'), preprint_updated=entry.findtext(ATOM + 'updated'),
                journal_reference=entry.findtext(ARXIV + 'journal_ref'),
                tags=[c.get('term') for c in entry.findall(ATOM + 'category') if c.get('term')],
                pdf_url=pdf, retrieval_source='arxiv_search', publication_year_source='unknown')
            assign_doi(paper, doi)
            assign_doi(paper, '10.48550/arxiv.' + re.sub(r'v\d+$', '', full_id, flags=re.I))
            papers.append(paper)
        outcome('arxiv_search', 'SUCCEEDED', query=query, effective_query=params.get('search_query'), count=len(papers))
        cache[cache_key] = copy.deepcopy(papers)
        return papers
    except Exception as exc:
        outcome('arxiv_search', 'FAILED', query=query, error_type=type(exc).__name__)
        return []


class _TitleMarkupParser(HTMLParser):
    """Remove recognized presentation markup while retaining scientific text."""
    _INLINE = {'i', 'b', 'em', 'strong', 'span', 'sub', 'sup', 'u', 's', 'a',
               'italic', 'bold', 'underline', 'sc', 'roman', 'overline',
               'monospace', 'inline-formula', 'tex-math', 'math', 'mi', 'mn',
               'mo', 'mrow', 'msub', 'msup', 'msubsup', 'mtext'}
    _BLOCK = {'br', 'p', 'div', 'title'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        local = tag.rsplit(':', 1)[-1]
        if local in self._BLOCK:
            self.parts.append(' ')
        elif local not in self._INLINE:
            self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        local = tag.rsplit(':', 1)[-1]
        if local in self._BLOCK:
            self.parts.append(' ')
        elif local not in self._INLINE:
            self.parts.append('</' + tag + '>')


def _title_text(value):
    parser = _TitleMarkupParser()
    parser.feed(str(value or ''))
    parser.close()
    return unescape(''.join(parser.parts))


def title_matches(left, right):
    def tokens(value):
        value = unicodedata.normalize('NFKD', _title_text(value)).encode('ascii', 'ignore').decode().lower()
        return re.findall(r'[a-z0-9]+', value)
    a, b = tokens(left or ''), tokens(right or '')
    # Only exact normalized titles establish a title-only identity. No first-hit acceptance.
    return bool(a) and a == b


def crossref_lookup(doi=None, title=None):
    if doi and arxiv_doi_id(doi):
        outcome('crossref', 'NOT_APPLICABLE', input_id=doi, agency='datacite')
        return None
    params = {'mailto': os.getenv('CROSSREF_MAILTO') or os.getenv('OPENALEX_MAILTO', '')}
    url = 'https://api.crossref.org/works'
    if doi:
        normalized = s2_id(doi)
        if not normalized or not normalized.startswith('DOI:'):
            return None
        url += '/' + quote(normalized[4:], safe='')
    elif title:
        params.update({'query.bibliographic': title, 'rows': 5})
    else:
        return None
    key = (url, json.dumps(params, sort_keys=True))
    with _cache_reads('crossref_cache', [key]) as cache:
        return _crossref_lookup_locked(doi, title, url, params, key, cache)


def _crossref_lookup_locked(doi, title, url, params, key, cache):
    if key in cache:
        value = copy.deepcopy(cache[key])
        outcome('crossref', 'SUCCEEDED' if value is not None else 'NOT_FOUND',
                input_id=doi, title=title, cache_hit=True)
        return value
    try:
        response = get(url, params=params)
        if response.status_code == 404:
            outcome('crossref', 'NOT_FOUND', input_id=doi, title=title)
            cache[key] = None
            return None
        response.raise_for_status()
        body = response.json()['message']
        if not doi:
            items = [x for x in body['items'] if title_matches(title, (x.get('title') or [''])[0])]
            if len(items) != 1:
                outcome('crossref', 'UNRESOLVED', title=title)
                return None
            body = items[0]
        if doi and s2_id(body.get('DOI')) != s2_id(doi):
            outcome('crossref', 'IDENTITY_CONFLICT', input_id=doi)
            return None
        cache[key] = copy.deepcopy(body)
        outcome('crossref', 'SUCCEEDED', input_id=body.get('DOI'))
        return copy.deepcopy(body)
    except Exception as exc:
        outcome('crossref', 'FAILED', input_id=doi, title=title, error_type=type(exc).__name__)
        return None


def verify_publication(paper):
    if paper.identity_status == 'conflict':
        return paper
    assign_doi(paper, paper.doi)
    if paper.identity_status == 'conflict':
        return paper
    lookup_doi = paper.publication_doi or paper.external_doi
    if not lookup_doi:
        if paper.preprint_doi:
            return verify_datacite(paper, paper.preprint_doi)
        return paper
    data = crossref_lookup(doi=lookup_doi)
    if not data:
        # A Crossref 404 is scoped to Crossref, not a global DOI absence.
        cache = getattr(get_runtime(), 'crossref_cache', {})
        url = 'https://api.crossref.org/works/' + quote((s2_id(lookup_doi) or '')[4:], safe='')
        params = {'mailto': os.getenv('CROSSREF_MAILTO') or os.getenv('OPENALEX_MAILTO', '')}
        key = (url, json.dumps(params, sort_keys=True))
        if key in cache and cache[key] is None and doi_agency(lookup_doi) == 'datacite':
            return verify_datacite(paper, lookup_doi)
        return paper
    title = (data.get('title') or [''])[0]
    if not title_matches(paper.title, title):
        paper.field_conflicts['crossref_title'] = [paper.title, title]
        paper.pdf_url = None  # Conflicting identity cannot authorize a PDF.
        paper.identity_status = 'conflict'
        outcome('crossref_identity', 'IDENTITY_CONFLICT', input_id=paper.doi)
        return paper
    record = {'source': 'crossref', 'metadata': data}
    if record not in paper.source_records:
        paper.source_records.append(record)
    paper.metadata_agency = 'crossref'
    paper.publication_type = data.get('type')
    paper.identity_status = 'verified_doi_title'
    if paper.publication_type not in ('journal-article', 'proceedings-article', 'book-chapter', 'book', 'monograph', 'edited-book', 'reference-entry'):
        paper.publication_status = 'preprint' if paper.publication_type == 'posted-content' and data.get('subtype') == 'preprint' else 'metadata_verified'
        if paper.publication_status == 'preprint':
            paper.preprint_metadata_dates['crossref'] = {k: data[k] for k in ('published', 'posted', 'created') if k in data}
        outcome('publication_type', 'UNCONFIRMED', input_id=paper.doi, record_type=paper.publication_type)
        return paper
    paper.publication_status = 'published_metadata_verified'
    paper.publication_doi = lookup_doi
    paper.publication_dates = {k: data[k] for k in ('published', 'published-print', 'published-online') if k in data}
    date = (data.get('published') or {}).get('date-parts', [[]])[0]
    if date:
        if paper.year is not None and paper.year != date[0]:
            paper.field_conflicts['year'] = list(dict.fromkeys(paper.field_conflicts.get('year', []) + [paper.year, date[0]]))
        paper.year = date[0]
        paper.publication_year_source = 'crossref'
        paper.publication_date = '-'.join(str(n).zfill(4 if i == 0 else 2) for i, n in enumerate(date))
    paper.venue = (data.get('container-title') or [paper.venue])[0]
    return paper


def verify_datacite(paper, doi):
    """DOI metadata does not establish a publication year or a PDF version."""
    is_arxiv = bool(arxiv_doi_id(doi))
    if is_arxiv and paper.preprint_published and paper.retrieval_source == 'arxiv_search':
        paper.metadata_agency = 'datacite'
        paper.publication_type, paper.publication_status = 'preprint', 'preprint'
        outcome('datacite', 'REUSED_ARXIV_METADATA', input_id=doi)
        return paper
    attrs = datacite_lookup(doi)
    if not attrs:
        return paper
    titles = [x.get('title') for x in attrs.get('titles', []) if isinstance(x, dict)]
    if not any(title_matches(paper.title, title) for title in titles):
        paper.field_conflicts['datacite_title'] = [paper.title] + titles
        paper.identity_status, paper.pdf_url = 'conflict', None
        outcome('datacite_identity', 'IDENTITY_CONFLICT', input_id=doi)
        return paper
    paper.metadata_agency = 'datacite'
    paper.publication_type = (attrs.get('types') or {}).get('resourceTypeGeneral')
    paper.publication_status = 'preprint' if is_arxiv or str(paper.publication_type).lower() == 'preprint' else 'metadata_verified'
    paper.identity_status = 'verified_doi_title'
    paper.preprint_metadata_dates['datacite'] = {k: attrs[k] for k in ('created', 'registered', 'dates', 'publicationYear') if k in attrs}
    record = {'source': 'datacite', 'metadata': attrs}
    if record not in paper.source_records:
        paper.source_records.append(record)
    return paper


def discover(query, years, limit=20, cfg=None):
    from pipelines.identity import upsert_paper
    from pipelines.state import PipelineState, Intent
    state = PipelineState(intent=Intent(task_type='retrieval', topic=query))
    rows = []
    for source_ordinal, (provider, found) in enumerate((
        ('semantic_scholar', search_s2(query, years, limit)),
        ('arxiv', search_arxiv(query, limit)),
    )):
        for rank, source_paper in enumerate(found, 1):
            paper = copy.deepcopy(source_paper)
            paper.source_records.append({'source': 'retrieval_hit', 'provider': provider,
                'query': query, 'effective_query': plain_query(query) if provider == 'semantic_scholar' else arxiv_query(query),
                'rank': rank, 'source_ordinal': source_ordinal,
                'requested_years': list(years) if years else None,
                'retrieval_limit': limit})
            assign_doi(paper, paper.doi)
            rows.append(paper)
    if staged_retrieval_enabled(cfg):
        for paper in rows:
            if years and paper.year is not None and not years[0] <= paper.year <= years[1]:
                outcome('year_filter', 'OUT_OF_SCOPE', title=paper.title, year=paper.year,
                        scope=list(years), phase='discovery', source_records=paper.source_records)
                continue
            upsert_paper(state, paper, source=paper.retrieval_source)
        outcome('candidate_metadata', 'DEFERRED', phase='discovery', query=query,
                input_count=len(rows), candidate_count=len(state.papers),
                paper_ids=list(state.papers), policy='shortlist_only')
        return list(state.papers.values())
    # Batch arXiv/DOI candidates that search did not already enrich via S2.
    missing = [p for p in rows if not p.s2_id and paper_lookup_id(p)]
    enriched = batch_metadata([paper_lookup_id(p) for p in missing], getattr(cfg, 's2_batch_size', 100)) if missing else []
    for paper, item in zip(missing, enriched):
        if item['status'] == 'SUCCEEDED':
            detail = paper_from_s2(item['data'], 's2_batch')
            if not title_matches(paper.title, detail.title):
                paper.identity_status = 'conflict'
                outcome('arxiv_s2_identity', 'IDENTITY_CONFLICT', input_id=paper.arxiv_id)
            else:
                paper.s2_id, paper.year, paper.venue = detail.s2_id, detail.year, detail.venue
                assign_doi(paper, paper.doi)
                assign_doi(paper, detail.doi)
                if detail.preprint_doi:
                    assign_doi(paper, detail.preprint_doi)
                paper.citation_count, paper.citation_count_source = detail.citation_count, detail.citation_count_source
                paper.publication_year_source = detail.publication_year_source
    for paper in rows:
        verify_publication(paper)
        # Keep unknown years visible; explicitly known out-of-window publications
        # remain in retrieval evidence, not in this bucket's candidate set.
        if years and paper.year is not None and not years[0] <= paper.year <= years[1]:
            outcome('year_filter', 'OUT_OF_SCOPE', title=paper.title, year=paper.year, scope=list(years))
            continue
        upsert_paper(state, paper, source=paper.retrieval_source)
    return list(state.papers.values())


def _has_s2_metadata(paper):
    """Null fields in an obtained S2 record are not a reason to refetch it."""
    sid = s2_id(paper.s2_id)
    if not sid:
        return False
    return any(isinstance(record, dict) and isinstance(record.get('metadata'), dict)
               and record.get('source') != 'shortlist_s2_invalid'
               and s2_id(record['metadata'].get('paperId')) == sid
               and record.get('fields', FIELDS) == FIELDS
               for record in paper.source_records)


def _metadata_conflicts(paper, detail):
    """Compare supplied strong IDs; absent fields are not contradictions."""
    pairs = {
        's2_id': (s2_id(paper.s2_id), s2_id(detail.s2_id)),
        'arxiv_id': (s2_lookup_id(paper.arxiv_id), s2_lookup_id(detail.arxiv_id)),
        'external_doi': (s2_id(paper.publication_doi or paper.external_doi),
                         s2_id(detail.publication_doi or detail.external_doi)),
        'preprint_doi': (s2_lookup_id(paper.preprint_doi), s2_lookup_id(detail.preprint_doi)),
    }
    return {field: [old, new] for field, (old, new) in pairs.items() if old and new and old != new}


def _remember_conflict(paper, field, old, value):
    values = paper.field_conflicts.setdefault(field, [])
    for item in (old, value):
        if item not in values:
            values.append(copy.deepcopy(item))


def _apply_shortlist_metadata(paper, detail, identifier):
    for record in detail.source_records:
        if record not in paper.source_records:
            paper.source_records.append(copy.deepcopy(record))
    conflicts = _metadata_conflicts(paper, detail)
    if not title_matches(paper.title, detail.title):
        conflicts['s2_title'] = [paper.title, detail.title]
    if detail.identity_status == 'conflict':
        conflicts['s2_record_identity'] = [copy.deepcopy(detail.field_conflicts)]
    if conflicts:
        for field, values in conflicts.items():
            paper.field_conflicts[field] = values
        paper.identity_status, paper.pdf_url = 'conflict', None
        outcome('shortlist_s2_identity', 'IDENTITY_CONFLICT', input_id=identifier,
                paper_id=paper.paper_id, conflicts=conflicts)
        return
    paper.s2_id = paper.s2_id or detail.s2_id
    # Preserve the requested material version and all existing source fields.
    # A work-level S2 response may supply missing bibliographic fields, but may
    # not overwrite an explicit arXiv version or its URL with the latest PDF.
    for field in ('abstract', 'authors', 'year', 'venue', 'publication_date', 'fields_of_study'):
        old, value = getattr(paper, field), getattr(detail, field)
        if value is None or value == '' or value == []:
            continue
        if old is None or old == '' or old == []:
            setattr(paper, field, copy.deepcopy(value))
            if field == 'year':
                paper.publication_year_source = detail.publication_year_source
        elif old != value:
            _remember_conflict(paper, field, old, value)
    if detail.citation_count is not None:
        if paper.citation_count is not None and paper.citation_count != detail.citation_count:
            _remember_conflict(paper, 'citation_count', paper.citation_count, detail.citation_count)
        paper.citation_count = detail.citation_count
        paper.citation_count_source = detail.citation_count_source
    paper.arxiv_id = paper.arxiv_id or detail.arxiv_id
    if not paper.pdf_url and not paper.document_version and not re.search(r'v\d+$', paper.arxiv_id or '', re.I):
        paper.pdf_url = detail.pdf_url
    for value in (detail.doi, detail.preprint_doi, detail.external_doi):
        assign_doi(paper, value)


def enrich_candidates(papers, cfg=None):
    """Enrich exactly the ordered shortlist; return the same paper objects.

    No candidate is added, discarded, merged or rekeyed. The caller owns the
    post-verification year/identity/quota decision and alias-index refresh.
    Costly S2 lookups are batched only where no S2 metadata record is present;
    publication verification is restricted to these supplied candidates.
    """
    candidates = list(papers)
    invalid_records = set()
    missing = []
    for paper in candidates:
        assign_doi(paper, paper.doi)
        if paper.identity_status != 'conflict' and not _has_s2_metadata(paper):
            identifier = paper_lookup_id(paper)
            if identifier:
                missing.append((paper, identifier))
    enriched = batch_metadata([identifier for _, identifier in missing],
                             getattr(cfg, 's2_batch_size', 100)) if missing else []
    if len(enriched) != len(missing):
        raise ValueError('Shortlist metadata must preserve all requested result slots')
    for (paper, identifier), item in zip(missing, enriched):
        if item.get('status') != 'SUCCEEDED':
            outcome('shortlist_metadata', item.get('status', 'INVALID_RESPONSE'),
                    input_id=identifier, paper_id=paper.paper_id)
            if item.get('status') == 'IDENTITY_CONFLICT':
                paper.identity_status, paper.pdf_url = 'conflict', None
            continue
        data = item.get('data')
        if not isinstance(data, dict) or not _matches_id(identifier, data):
            paper.identity_status, paper.pdf_url = 'conflict', None
            outcome('shortlist_s2_identity', 'IDENTITY_CONFLICT', input_id=identifier,
                    paper_id=paper.paper_id)
            continue
        try:
            detail = paper_from_s2(data, 'shortlist_s2_batch')
        except (ValueError, TypeError, AttributeError, KeyError) as exc:
            # One malformed provider record must not discard all other scored
            # candidates. Preserve raw evidence and distinguish this from a miss.
            paper.source_records.append({'source': 'shortlist_s2_invalid', 'metadata': data,
                                         'error_type': type(exc).__name__})
            outcome('shortlist_metadata', 'INVALID_RESPONSE', input_id=identifier,
                    paper_id=paper.paper_id, error_type=type(exc).__name__)
            invalid_records.add(id(paper))
            continue
        _apply_shortlist_metadata(paper, detail, identifier)
    for ordinal, paper in enumerate(candidates):
        if paper.identity_status != 'conflict':
            verify_publication(paper)
        if id(paper) in invalid_records:
            continue  # Keep INVALID_RESPONSE, do not also mark it COMPLETED.
        outcome('shortlist_metadata', 'IDENTITY_CONFLICT' if paper.identity_status == 'conflict' else 'COMPLETED',
                paper_id=paper.paper_id, ordinal=ordinal, publication_status=paper.publication_status,
                known_year=paper.year)
    return candidates


def _seed_identity_evidence(seed, paper):
    """Separate contradicted identifiers from identifiers absent in a response."""
    aliases = {s2_lookup_id(getattr(paper, field, None)) for field in
               ('s2_id', 'doi', 'arxiv_id', 'preprint_doi', 'external_doi', 'publication_doi')}
    aliases.discard(None)
    verified, unknown, conflicts = [], [], []
    for field in ('s2_id', 'doi', 'arxiv_id', 'url'):
        identifier = s2_lookup_id(seed.get(field))
        if not identifier:
            continue
        if identifier in aliases:
            verified.append(identifier)
            continue
        domain = identifier.split(':', 1)[0] if ':' in identifier else 'S2'
        present = [value for value in aliases if (value.split(':', 1)[0] if ':' in value else 'S2') == domain]
        if present:
            conflicts.append({'field': field, 'requested': identifier, 'returned': sorted(present)})
        else:
            unknown.append({'field': field, 'requested': identifier})
    return list(dict.fromkeys(verified)), unknown, conflicts


def _versioned_arxiv_fallbacks(seeds, identifiers, items):
    """Batch exact versions only; unversioned/latest lookups keep their path."""
    needed = []
    for seed, identifier, item in zip(seeds, identifiers, items):
        if item and item.get('status') in ('SUCCEEDED', 'IDENTITY_CONFLICT'):
            continue
        raw = [s2_id(seed.get(field)) for field in ('arxiv_id', 'url')]
        versions = {value for value in raw if value and value.startswith('ARXIV:')
                    and re.search(r'v\d+$', value, re.I)}
        if len(versions) == 1:
            needed.append(next(iter(versions)))
    needed = list(dict.fromkeys(needed))
    resolved = {}
    # Keep URI/request size bounded. This changes only transport grouping,
    # never the IDs, requested versions or candidate checks of a seed.
    for start in range(0, len(needed), 100):
        chunk = needed[start:start + 100]
        found = search_arxiv('', limit=len(chunk), id_list=[key[6:] for key in chunk])
        cache, _ = _run_cache('arxiv_cache')
        batch_key = ('https://export.arxiv.org/api/query', json.dumps(
            {'start': 0, 'max_results': len(chunk), 'sortBy': 'relevance',
             'id_list': ','.join(key[6:] for key in chunk)}, sort_keys=True))
        if len(chunk) > 1 and not found and batch_key not in cache:
            # A transport/invalid-feed failure is not evidence that every ID
            # is missing. Retain the previous bounded per-ID recovery path.
            outcome('arxiv_id_batch', 'FALLBACK', input_count=len(chunk),
                    reason='failed_batch_recover_individual_ids')
            found = []
            for requested in chunk:
                found.extend(search_arxiv('', limit=1, id_list=[requested[6:]]))
        for requested in chunk:
            same_work = [paper for paper in found
                         if s2_lookup_id(paper.arxiv_id) == s2_lookup_id(requested)]
            exact = [paper for paper in same_work if s2_id(paper.arxiv_id) == requested]
            # A wrong-version record is still passed through the existing
            # explicit conflict check rather than silently accepted or hidden.
            resolved[requested] = exact or same_work
    return resolved


def _validate_seeds_staged(seeds, cfg=None):
    """Corroborate seed identity while deferring publication enrichment."""
    seeds = list(seeds)
    identifiers = [paper_lookup_id(seed) for seed in seeds]
    lookup_ids = [value for value in identifiers if value]
    looked_up = iter(batch_metadata(lookup_ids, getattr(cfg, 's2_batch_size', 100)) if lookup_ids else [])
    items = [next(looked_up) if identifier else None for identifier in identifiers]
    arxiv_fallbacks = (_versioned_arxiv_fallbacks(seeds, identifiers, items)
                       if _optimized_requests(cfg) else {})
    results = []
    for seed, identifier, item in zip(seeds, identifiers, items):
        title = seed.get('title') or ''
        raw_arxiv = [s2_id(seed.get(field)) for field in ('arxiv_id', 'url')]
        raw_arxiv = [value for value in raw_arxiv if value and value.startswith('ARXIV:')]
        versions = {value for value in raw_arxiv if re.search(r'v\d+$', value, re.I)}
        if len(versions) > 1:
            outcome('seed_version', 'IDENTITY_CONFLICT', title=title, versions=sorted(versions))
            continue
        if item and item.get('status') == 'IDENTITY_CONFLICT':
            continue
        paper = None
        if item and item.get('status') == 'SUCCEEDED':
            candidate = paper_from_s2(item['data'], 'validated_s2_batch')
            verified, unknown, conflicts = _seed_identity_evidence(seed, candidate)
            if (not _matches_id(identifier, item['data']) or not title_matches(title, candidate.title)
                    or conflicts or candidate.identity_status == 'conflict'):
                outcome('seed_identity', 'IDENTITY_CONFLICT', input_id=identifier, title=title, conflicts=conflicts)
                continue
            paper = candidate
        if paper is None:
            # A valid arXiv record remains usable when S2 has not indexed it.
            # Query the exact requested version, not a broad title or latest PDF.
            exact_id = next(iter(versions), None) or next(iter(raw_arxiv), None)
            if not exact_id and identifier and identifier.startswith('ARXIV:'):
                exact_id = identifier
            if exact_id:
                if exact_id in arxiv_fallbacks:
                    candidates = copy.deepcopy(arxiv_fallbacks[exact_id])
                else:
                    candidates = search_arxiv('', limit=1, id_list=[exact_id[6:]])
            else:
                candidates = search_s2(title, limit=5)
                if not any(title_matches(title, candidate.title) for candidate in candidates):
                    candidates += search_arxiv(title, 5)
            eligible = []
            for candidate in candidates:
                if not title_matches(title, candidate.title):
                    continue
                if exact_id and re.search(r'v\d+$', exact_id, re.I) and s2_id(candidate.arxiv_id) != exact_id:
                    outcome('seed_version', 'IDENTITY_CONFLICT', title=title,
                            requested_version=exact_id, returned_version=candidate.arxiv_id)
                    continue
                verified, unknown, conflicts = _seed_identity_evidence(seed, candidate)
                if conflicts or candidate.identity_status == 'conflict':
                    outcome('seed_identity', 'IDENTITY_CONFLICT', input_id=identifier, title=title, conflicts=conflicts)
                    continue
                if identifier and not verified:
                    continue
                eligible.append(candidate)
            if len(eligible) == 1:
                paper = eligible[0]
            elif not eligible and not exact_id:
                # Retain the old final source for title-only/DOI seed identity,
                # but do not interpret its publication type/date at this stage.
                cr = crossref_lookup(doi=identifier[4:]) if identifier and identifier.startswith('DOI:') else crossref_lookup(title=title)
                if cr:
                    candidate = Paper(title=cr['title'][0], doi=cr.get('DOI'),
                        venue=(cr.get('container-title') or [None])[0],
                        authors=[' '.join(filter(None, [author.get('given'), author.get('family')])) for author in cr.get('author') or []],
                        retrieval_source='crossref_seed_identity', source_records=[{'source': 'crossref', 'metadata': cr}])
                    assign_doi(candidate, candidate.doi)
                    verified, unknown, conflicts = _seed_identity_evidence(seed, candidate)
                    if title_matches(title, candidate.title) and not conflicts and (not identifier or verified):
                        paper = candidate
        if paper is None:
            outcome('seed_validation', 'UNRESOLVED', title=title, phase='staged_identity')
            continue
        verified, unknown, conflicts = _seed_identity_evidence(seed, paper)
        if conflicts:
            outcome('seed_identity', 'IDENTITY_CONFLICT', input_id=identifier, title=title, conflicts=conflicts)
            continue
        if unknown:
            outcome('seed_identity', 'UNVERIFIED_IDENTITY_FIELDS', title=title,
                    verified_identifiers=verified, unverified_identifiers=unknown)
        source_id = next(iter(versions), None)
        if source_id:
            paper.arxiv_id, paper.document_version = source_id[6:], source_id[6:]
            paper.pdf_url = 'https://arxiv.org/pdf/' + source_id[6:]
        paper.source_records.append({'source': 'llm_seed_input', 'metadata': copy.deepcopy(seed),
            'verified_identifiers': verified, 'unverified_identifiers': unknown})
        outcome('publication_verification', 'DEFERRED', phase='seed_identity', title=title,
                input_id=identifier, policy='shortlist_only')
        results.append(paper)
    return results


def validate_seeds(seeds, cfg=None):
    if staged_retrieval_enabled(cfg):
        return _validate_seeds_staged(seeds, cfg)
    identifiers = [paper_lookup_id(seed) for seed in seeds]
    ids = [x for x in identifiers if x]
    batches = iter(batch_metadata(ids, getattr(cfg, 's2_batch_size', 100)))
    results = []
    for seed, identifier in zip(seeds, identifiers):
        title = seed.get('title') or ''
        paper = None
        if identifier:
            item = next(batches)
            if item['status'] == 'SUCCEEDED':
                candidate = paper_from_s2(item['data'], 'validated_s2_batch')
                if title_matches(title, candidate.title):
                    paper = candidate
                else:
                    outcome('seed_identity', 'IDENTITY_CONFLICT', input_id=identifier, title=title)
                    continue
            elif item['status'] == 'IDENTITY_CONFLICT':
                continue
        if paper is None:
            matches = [p for p in search_s2(title, limit=5) if title_matches(title, p.title) and seed_matches(seed, p)]
            if len(matches) == 1:
                paper = matches[0]
            else:
                cr = crossref_lookup(title=title)
                if cr:
                    paper = Paper(title=cr['title'][0], doi=cr.get('DOI'),
                        venue=(cr.get('container-title') or [None])[0],
                        authors=[' '.join(filter(None, [a.get('given'), a.get('family')])) for a in cr.get('author') or []],
                        retrieval_source='crossref_title', source_records=[{'source': 'crossref', 'metadata': cr}])
                else:
                    matches = [p for p in search_arxiv(title, 5) if title_matches(title, p.title) and seed_matches(seed, p)]
                    if len(matches) == 1:
                        paper = matches[0]
        if paper and not seed_matches(seed, paper):
            outcome('seed_identity', 'IDENTITY_CONFLICT', input_id=identifier, title=title)
            paper = None
        if paper:
            # S2's work-level record must not erase an explicitly supplied version.
            versions = {s2_id(seed.get(k)) for k in ('arxiv_id', 'url')
                        if re.search(r'v\d+$', s2_id(seed.get(k)) or '', re.I)
                        and (s2_id(seed.get(k)) or '').startswith('ARXIV:')}
            if len(versions) > 1:
                outcome('seed_version', 'IDENTITY_CONFLICT', title=title, versions=sorted(versions))
                continue
            source_id = next(iter(versions), None)
            if source_id and re.search(r'v\d+$', source_id, re.I):
                paper.arxiv_id = source_id[6:]
                paper.document_version = source_id[6:]
                paper.pdf_url = 'https://arxiv.org/pdf/' + source_id[6:]
            paper.source_records.append({'source': 'llm_seed_input', 'metadata': seed})
            results.append(verify_publication(paper))
        else:
            outcome('seed_validation', 'UNRESOLVED', title=title)
    # Resolve IDs found through Crossref/arXiv together, not singleton calls.
    missing = [p for p in results if not p.s2_id and paper_lookup_id(p)]
    enriched = batch_metadata([paper_lookup_id(p) for p in missing], getattr(cfg, 's2_batch_size', 100)) if missing else []
    for paper, item in zip(missing, enriched):
        if item['status'] == 'SUCCEEDED' and title_matches(paper.title, item['data'].get('title')):
            detail = paper_from_s2(item['data'])
            if paper.identity_status == 'conflict':
                continue
            paper.s2_id = detail.s2_id
            paper.abstract = paper.abstract or detail.abstract
            paper.citation_count, paper.citation_count_source = detail.citation_count, detail.citation_count_source
            paper.arxiv_id = paper.arxiv_id or detail.arxiv_id
            paper.pdf_url = paper.pdf_url or detail.pdf_url
            assign_doi(paper, detail.doi)
            if paper.year is None:
                paper.year, paper.publication_year_source = detail.year, detail.publication_year_source
            verify_publication(paper)
    return results


def seed_matches(seed, paper):
    """A title fallback cannot override any explicitly supplied strong identity."""
    aliases = {s2_lookup_id(getattr(paper, k, None)) for k in ('s2_id', 'doi', 'arxiv_id', 'preprint_doi', 'external_doi', 'publication_doi')}
    supplied = [s2_lookup_id(seed.get(k)) for k in ('s2_id', 'doi', 'arxiv_id', 'url')]
    return all(identifier in aliases for identifier in supplied if identifier)
