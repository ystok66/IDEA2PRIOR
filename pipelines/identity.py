"""Explicit paper aliases and immutable material identities; no fuzzy-title merge."""
from __future__ import annotations

import hashlib
import json
import re
import copy
from typing import Any
from urllib.parse import unquote

from pipelines.state import Paper, PipelineState


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    value = unquote(value).strip()
    value = re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:)', '', value, flags=re.I)
    return value.strip().casefold() or None


def normalize_openalex(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().rstrip('/').rsplit('/', 1)[-1].upper()


def normalize_arxiv(value: str | None) -> str | None:
    if not value:
        return None
    value = re.sub(r'^https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/', '', value.strip(), flags=re.I)
    return re.sub(r'\.pdf$', '', re.sub(r'^arxiv:', '', value, flags=re.I), flags=re.I).casefold()


def paper_aliases(paper: Paper) -> list[str]:
    aliases = []
    for prefix, value in [('doi', normalize_doi(paper.doi)), ('doi', normalize_doi(paper.preprint_doi)),
                          ('doi', normalize_doi(paper.publication_doi)), ('doi', normalize_doi(paper.external_doi)), ('openalex', normalize_openalex(paper.openalex_id)), ('s2', (paper.s2_id or '').strip().casefold())]:
        if value:
            aliases.append(f'{prefix}:{value}')
    arxiv = normalize_arxiv(paper.arxiv_id)
    if arxiv:
        aliases.append('arxiv:' + arxiv)
        aliases.append('arxiv-work:' + re.sub(r'v\d+$', '', arxiv))
    return list(dict.fromkeys(aliases))


def _version(paper: Paper) -> str | None:
    if paper.document_version:
        version = re.sub(r'^arxiv:', '', paper.document_version, flags=re.I)
        if re.fullmatch(r'(?:\d{4}\.\d{4,5}|[a-z.-]+/\d{7})v\d+', version, re.I):
            return 'arxiv:' + version.casefold()
        return paper.document_version
    arxiv = normalize_arxiv(paper.arxiv_id)
    match = re.search(r'v\d+$', arxiv or '')
    return 'arxiv:' + arxiv if match else None


def _conflict(a: Paper, b: Paper) -> bool:
    # Conflicting strong identities require review, not a majority vote.
    def publication_doi(p):
        doi = normalize_doi(p.publication_doi or p.external_doi or p.doi)
        return None if (doi or '').startswith('10.48550/arxiv.') else doi
    da, db = normalize_doi(a.doi), normalize_doi(b.doi)
    wa = re.sub(r'v\d+$', '', normalize_arxiv(a.arxiv_id) or '')
    wb = re.sub(r'v\d+$', '', normalize_arxiv(b.arxiv_id) or '')
    linked_preprint = bool(wa and wa == wb and ((da or '').startswith('10.48550/arxiv.') or (db or '').startswith('10.48550/arxiv.')))
    return any(x and y and x != y for x, y in [
        (publication_doi(a), publication_doi(b)),
        (normalize_doi(a.preprint_doi), normalize_doi(b.preprint_doi)),
        (None if linked_preprint else da, db),
        (normalize_openalex(a.openalex_id), normalize_openalex(b.openalex_id)),
        ((a.s2_id or '').casefold(), (b.s2_id or '').casefold()),
    ])


def _same_material(a: Paper, b: Paper) -> bool:
    va, vb = _version(a), _version(b)
    if va != vb and (va and vb or a.pdf_url or b.pdf_url or a.local_pdf_path or b.local_pdf_path
                     or a.chunks or b.chunks or a.parsed_sections or b.parsed_sections):
        return False
    if a.content_hash and b.content_hash and a.content_hash != b.content_hash:
        return False
    return True


def _source(paper: Paper, source: str) -> dict[str, Any]:
    return {'source': source, 'metadata': {k: copy.deepcopy(getattr(paper, k)) for k in (
        'title', 'year', 'venue', 'abstract', 'authors', 'doi', 'openalex_id', 's2_id', 'arxiv_id',
        'citation_count', 'pdf_url', 'document_version', 'content_hash', 'citation_count_source',
        'publication_year_source', 'publication_dates', 'preprint_published', 'preprint_updated',
        'journal_reference', 'identity_status', 'preprint_doi', 'external_doi', 'publication_doi',
        'publication_status', 'publication_type', 'metadata_agency', 'preprint_metadata_dates')}}


def upsert_paper(state: PipelineState, paper: Paper, *, source: str = 'unknown') -> tuple[str, bool]:
    """Return stable record key/new flag, preserving source conflicts and versions."""
    paper.doi = normalize_doi(paper.doi)
    paper.openalex_id = normalize_openalex(paper.openalex_id)
    paper.arxiv_id = normalize_arxiv(paper.arxiv_id)
    paper.document_version = _version(paper)
    aliases = paper_aliases(paper)
    candidates = list(dict.fromkeys(pid for alias in aliases for pid in state.paper_aliases.get(alias, []) if pid in state.papers))
    if paper.paper_id in state.papers and paper.paper_id not in candidates:
        candidates.append(paper.paper_id)
    compatible = [pid for pid in candidates if not _conflict(state.papers[pid], paper) and _same_material(state.papers[pid], paper)]
    if len(compatible) == 1:
        pid = compatible[0]
        current = state.papers[pid]
        prefer_publication = (paper.publication_status == 'published_metadata_verified'
                              and paper.identity_status == 'verified_doi_title'
                              and current.publication_status != 'published_metadata_verified')
        record = _source(paper, source)
        if record not in current.source_records:
            current.source_records.append(record)
        for incoming in paper.source_records:
            if incoming not in current.source_records:
                current.source_records.append(incoming)
        for name in ('publication_date', 'publication_year_source', 'publication_dates',
                     'preprint_published', 'preprint_updated', 'journal_reference', 'citation_count_source', 'fields_of_study',
                     'preprint_doi', 'external_doi', 'publication_doi', 'publication_type', 'metadata_agency', 'preprint_metadata_dates'):
            value, old = getattr(paper, name), getattr(current, name)
            if value and not old:
                setattr(current, name, value)
            elif value and old != value:
                current.field_conflicts.setdefault(name, [old]).append(value)
        if current.publication_status == 'unverified':
            current.publication_status = paper.publication_status
        if paper.identity_status == 'conflict':
            current.identity_status = 'conflict'
            current.pdf_url = None
        elif current.identity_status == 'unverified':
            current.identity_status = paper.identity_status
        for name in ('title', 'abstract', 'year', 'venue', 'authors', 'doi', 'openalex_id', 'arxiv_id', 's2_id', 'citation_count', 'pdf_url', 'document_version', 'content_hash'):
            value, old = getattr(paper, name), getattr(current, name)
            if value is None or value == '' or value == []:
                continue
            if old is None or old == '' or old == []:
                setattr(current, name, value)
            elif old != value:
                values = current.field_conflicts.setdefault(name, [old])
                if value not in values:
                    values.append(value)
        # Parsed artifacts only transfer when an actual content identity matches.
        if prefer_publication:
            for name in ('year', 'venue', 'publication_date', 'publication_year_source', 'publication_dates',
                         'publication_status', 'publication_type', 'metadata_agency'):
                if getattr(paper, name):
                    setattr(current, name, getattr(paper, name))
        if current.publication_doi:
            current.doi = current.publication_doi
        if current.identity_status == 'conflict':
            current.pdf_url = None
        if current.content_hash and current.content_hash == paper.content_hash:
            for name in ('chunks', 'parsed_sections', 'local_pdf_path'):
                if not getattr(current, name) and getattr(paper, name):
                    setattr(current, name, getattr(paper, name))
        created = False
    else:
        strong_related = [state.papers[pid] for pid in candidates if not _conflict(state.papers[pid], paper)]
        # Unresolved records stay separate, but repeated replay of an ordered
        # source sequence yields the same identities (no title-based merging).
        base = aliases[0] if aliases else 'unresolved:' + json.dumps(
            {'source': _source(paper, source), 'ordinal': len(state.papers)}, sort_keys=True)
        paper.entity_id = (strong_related[0].entity_id if strong_related else paper.entity_id) or 'e_' + hashlib.sha256(base.encode()).hexdigest()[:24]
        material = '|'.join([paper.entity_id, paper.document_version or 'unspecified', paper.content_hash or ''])
        pid = paper.paper_id or 'p_' + hashlib.sha256(material.encode()).hexdigest()[:24]
        if pid in state.papers:
            base_pid, suffix = pid, 2
            while pid in state.papers:
                pid = f'{base_pid}_{suffix}'
                suffix += 1
        paper.paper_id = pid
        paper.aliases = aliases
        paper.source_records.append(_source(paper, source))
        state.papers[pid] = paper
        current = paper
        created = True
        if candidates and (not strong_related or len(compatible) > 1):
            state.identity_conflicts.append({'new_id': pid, 'existing_ids': candidates, 'reason': 'strong_id_conflict_or_ambiguous_aliases'})
    for alias in aliases:
        ids = state.paper_aliases.setdefault(alias, [])
        if pid not in ids:
            ids.append(pid)
        if alias not in current.aliases:
            current.aliases.append(alias)
    return pid, created


def normalize_state(state: PipelineState) -> dict[str, str]:
    """Migrate legacy dictionary keys without discarding incoming source records."""
    original = list(state.papers.items())
    state.papers = {}
    state.paper_aliases = {}
    mapping = {}
    for old_key, paper in original:
        new_key, _ = upsert_paper(state, paper, source=paper.retrieval_source or 'legacy_state')
        mapping[old_key] = new_key
    for edge in state.citation_edges:
        edge.source_id = mapping.get(edge.source_id, edge.source_id)
        edge.target_id = mapping.get(edge.target_id, edge.target_id)
    return mapping


def paper_from_openalex(work: dict[str, Any], *, source: str = 'openalex') -> Paper:
    abstract = work.get('abstract')
    index = work.get('abstract_inverted_index')
    if not abstract and isinstance(index, dict):
        words = sorted((position, word) for word, positions in index.items() for position in positions)
        abstract = ' '.join(word for _, word in words)
    authors = work.get('authors') or [a.get('author', {}).get('display_name', '') for a in work.get('authorships', [])]
    authors = [a.get('name') or a.get('display_name', '') if isinstance(a, dict) else str(a) for a in authors]
    location = work.get('primary_location') or {}
    venue = work.get('venue') or (location.get('source') or {}).get('display_name')
    return Paper(title=work.get('title') or work.get('display_name') or 'Unknown Title',
                 abstract=abstract, authors=authors, year=work.get('year') or work.get('publication_year'),
                 venue=venue, doi=normalize_doi(work.get('doi')), openalex_id=normalize_openalex(work.get('openalex_id') or work.get('id')),
                 citation_count=work.get('cited_by_count', work.get('citation_count')), pdf_url=work.get('pdf_url') or location.get('pdf_url'),
                 retrieval_source=source)
