"""Consume already obtained Crossref references once, without recursive crawling."""
from __future__ import annotations

import copy
import html
import re
from collections import Counter

from pipelines.clients import scholarly
from pipelines.identity import paper_aliases, upsert_paper
from pipelines.runtime import atomic_write_json, fingerprint
from pipelines.state import Paper, PipelineState
from pipelines.tasks.citation_neighbor_filter import _key
from pipelines.tasks.prior_art_utils import is_excluded_paper, ordered_parallel_map

POLICY = 'crossref_reference_ingestion_v1'


def _text(value):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', str(value or '')))).strip()


def _title(value):
    return re.sub(r'\s+', ' ', scholarly._title_text(value)).strip()


def _title_key(title):
    # Same exact normalized-title contract as scholarly.title_matches.
    import unicodedata
    plain = unicodedata.normalize('NFKD', scholarly._title_text(title)).encode('ascii', 'ignore').decode().lower()
    return ' '.join(re.findall(r'[a-z0-9]+', plain))


def _unstructured_doi(value):
    """Use only explicit DOI strings, never infer an ID from a citation's title."""
    found = set()
    for match in re.findall(r'10\.\d{4,9}/[^\s<>"\']+', _text(value)):
        candidate = match.rstrip('.,;')
        for left, right in [('(', ')'), ('[', ']')]:
            while candidate.endswith(right) and candidate.count(right) > candidate.count(left):
                candidate = candidate[:-1]
        identifier = scholarly.s2_id(candidate)
        if identifier:
            found.add(identifier)
    return next(iter(found)) if len(found) == 1 else None


def collect_crossref_references(state):
    """Snapshot parent records. Reference years are evidence, never an early gate."""
    items, decisions, reference_count = {}, [], 0
    for parent_id, parent in list(state.papers.items()):
        seen = set()
        for source in list(parent.source_records):
            if not isinstance(source, dict) or source.get('source') != 'crossref':
                continue
            metadata = source.get('metadata')
            if not isinstance(metadata, dict) or not isinstance(metadata.get('reference'), list):
                continue
            digest = fingerprint(metadata)
            if digest in seen:
                continue
            seen.add(digest)
            supplied_title = (metadata.get('title') or [''])[0]
            supplied_doi = scholarly.s2_id(metadata.get('DOI'))
            parent_dois = {scholarly.s2_id(getattr(parent, name, None)) for name in
                           ('doi', 'external_doi', 'publication_doi', 'preprint_doi')}
            parent_dois.discard(None)
            incompatible = (parent.identity_status == 'conflict'
                            or (supplied_title and not scholarly.title_matches(parent.title, supplied_title))
                            or (supplied_doi and parent_dois and supplied_doi not in parent_dois))
            if incompatible:
                decisions.append({'parent_id': parent_id, 'action': 'parent_identity_conflict'})
                continue
            for index, reference in enumerate(metadata['reference']):
                reference_count += 1
                origin = {'parent_id': parent_id, 'parent_doi': metadata.get('DOI'),
                          'reference_index': index, 'reference': copy.deepcopy(reference)}
                if not isinstance(reference, dict):
                    decisions.append({**origin, 'action': 'invalid_reference'})
                    continue
                doi = scholarly.s2_id(reference.get('DOI'))
                doi = doi if doi and doi.startswith('DOI:') else None
                if not doi and not reference.get('DOI'):
                    doi = _unstructured_doi(reference.get('unstructured'))
                title = _title(reference.get('article-title'))
                if not doi and not title:
                    decisions.append({**origin, 'action': 'unresolved_reference_no_doi_or_article_title'})
                    continue
                if not doi and not _title_key(title):
                    decisions.append({**origin, 'action': 'unresolved_title_identity'})
                    continue
                key = doi or 'TITLE:' + _title_key(title)
                item = items.setdefault(key, {'key': key, 'doi': doi[4:] if doi else None,
                                              'title': title, 'titles': [], 'origins': []})
                if title and title not in item['titles']:
                    item['titles'].append(title)
                item['origins'].append(origin)
    audit = {'policy': POLICY, 'reference_count': reference_count, 'unique_reference_count': len(items),
             'collection_decisions': decisions, 'recursive_expansion': False,
             'input_parent_count': len(state.papers)}
    return list(items.values()), audit


def _compatible_titles(item, title):
    return all(scholarly.title_matches(value, title) for value in item['titles'])


def _from_crossref(data):
    title = (data.get('title') or [''])[0]
    if not title:
        return None
    paper = Paper(title=_title(title), doi=data.get('DOI'),
                  authors=[' '.join(filter(None, (a.get('given'), a.get('family'))))
                           for a in data.get('author') or []],
                  venue=(data.get('container-title') or [None])[0],
                  abstract=_text(data.get('abstract')) or None,
                  retrieval_source='crossref_reference',
                  source_records=[{'source': 'crossref', 'metadata': copy.deepcopy(data)}])
    scholarly.assign_doi(paper, paper.doi)
    # This reuses the same-run Crossref cache and the established publication rules.
    scholarly.verify_publication(paper)
    return paper


def build_crossref_reference_groups(state, cfg, run_mgr=None):
    """Resolve unique references; return per-parent groups + typed registration map.

    DOI metadata is batched; title-only identity requires one exact title match.
    No new parent metadata requests, keyword prefilter, or reference pre-cut is added.
    Known candidate identities are reused and not semantically scored a second time.
    """
    items, audit = collect_crossref_references(state)
    decisions, pending = [], []
    existing = {}
    for paper in state.papers.values():
        for alias in paper_aliases(paper):
            existing.setdefault(alias, []).append(paper)
    for item in items:
        known = existing.get('doi:' + item['doi'], []) if item['doi'] else []
        compatible = [p for p in known if p.identity_status != 'conflict' and _compatible_titles(item, p.title)]
        if known:
            if compatible and len({p.entity_id or p.paper_id for p in compatible}) == 1:
                for paper in compatible:
                    record = {'source': 'crossref_reference_context', 'origins': item['origins']}
                    if record not in paper.source_records:
                        paper.source_records.append(copy.deepcopy(record))
                decisions.append({'reference_key': item['key'], 'action': 'already_registered',
                                  'paper_ids': [p.paper_id for p in compatible], 'origins': item['origins']})
            else:
                decisions.append({'reference_key': item['key'], 'action': 'existing_identity_conflict',
                                  'origins': item['origins']})
            continue
        pending.append(item)
    doi_items = [item for item in pending if item['doi']]
    looked_up = scholarly.batch_metadata([item['key'] for item in doi_items], cfg.s2_batch_size) if doi_items else []
    if len(looked_up) != len(doi_items):
        raise ValueError('Crossref reference metadata must preserve every requested slot')
    by_doi = {item['key']: response for item, response in zip(doi_items, looked_up)}

    def resolve(item):
        try:
            paper, status = None, 'unresolved_identity'
            response = by_doi.get(item['key'])
            if response and response.get('status') == 'IDENTITY_CONFLICT':
                return item, None, 'identity_conflict'
            if response and response.get('status') == 'SUCCEEDED':
                data = response.get('data')
                identifier = scholarly.s2_lookup_id(item['key'])
                if not isinstance(data, dict) or not scholarly._matches_id(identifier, data):
                    return item, None, 'identity_conflict'
                paper = scholarly.paper_from_s2(data, 'crossref_reference_s2')
                scholarly.assign_doi(paper, item['doi'])
                if not _compatible_titles(item, paper.title) or paper.identity_status == 'conflict':
                    return item, None, 'identity_conflict'
                paper.identity_status = 'verified_s2_identifier'
            if not item['doi']:
                found = scholarly.search_s2(item['title'], limit=5)
                matching = [p for p in found if _compatible_titles(item, p.title)
                            and p.identity_status != 'conflict' and (p.s2_id or p.doi or p.arxiv_id)]
                identities = {scholarly.paper_lookup_id(p) for p in matching}
                if len(identities) == 1 and matching:
                    paper = matching[0]
                    paper.identity_status = 'verified_exact_title'
                elif matching:
                    return item, None, 'ambiguous_title'
            if paper is None or not paper.abstract:
                cr = scholarly.crossref_lookup(doi=item['doi']) if item['doi'] else scholarly.crossref_lookup(title=item['title'])
                if cr:
                    if item['doi'] and scholarly.s2_id(cr.get('DOI')) != item['key']:
                        return item, None, 'identity_conflict'
                    if not _compatible_titles(item, (cr.get('title') or [''])[0]):
                        return item, None, 'identity_conflict'
                    detail = _from_crossref(cr)
                    if detail is not None and detail.identity_status == 'conflict':
                        return item, None, 'identity_conflict'
                    if detail and detail.identity_status != 'conflict':
                        if paper is None:
                            paper = detail
                        else:
                            if (not scholarly.title_matches(paper.title, detail.title)
                                    or scholarly._metadata_conflicts(paper, detail)):
                                return item, None, 'identity_conflict'
                            paper.abstract = paper.abstract or detail.abstract
                            paper.source_records.extend(detail.source_records)
                            # Prefer the authoritative publication date/type over reference years.
                            for field in ('year', 'venue', 'publication_dates', 'publication_status',
                                          'publication_doi', 'publication_type', 'metadata_agency'):
                                value = getattr(detail, field)
                                if value:
                                    setattr(paper, field, copy.deepcopy(value))
            if paper is None:
                return item, None, status
            if paper.identity_status == 'conflict' or not _compatible_titles(item, paper.title):
                return item, None, 'identity_conflict'
            if is_excluded_paper(paper, cfg):
                return item, None, 'excluded_by_protocol'
            paper.retrieval_source = 'crossref_reference'
            paper.source_records.append({'source': 'crossref_reference_context', 'origins': item['origins']})
            return item, paper, 'resolved' if paper.abstract else 'resolved_missing_abstract'
        except Exception as error:
            return item, None, 'resolution_failed:' + type(error).__name__

    resolved, groups, outcomes = {}, {}, []
    for item, paper, action in ordered_parallel_map(pending, resolve, max_workers=cfg.max_workers_s2):
        decision = {'reference_key': item['key'], 'action': action, 'origins': item['origins']}
        if paper is not None:
            # A title-only citation may resolve to a DOI already present in the corpus.
            alias_hits = [p for alias in paper_aliases(paper) for p in existing.get(alias, [])]
            if alias_hits:
                if any(p.identity_status == 'conflict' or not scholarly.title_matches(p.title, paper.title)
                       or scholarly._metadata_conflicts(p, paper)
                       for p in alias_hits):
                    decision['action'] = 'existing_identity_conflict'
                else:
                    for known in alias_hits:
                        record = paper.source_records[-1]
                        if record not in known.source_records:
                            known.source_records.append(copy.deepcopy(record))
                    decision['action'] = 'already_registered'
                outcomes.append(decision)
                continue
            row = {'paperId': paper.s2_id, 'title': paper.title, 'abstract': paper.abstract,
                   'year': paper.year, 'venue': paper.venue,
                   'externalIds': {'DOI': paper.doi, 'ArXiv': paper.arxiv_id},
                   'authors': [{'name': name} for name in paper.authors],
                   'citationCount': paper.citation_count}
            key = _key(row)
            previous = resolved.get(key)
            if previous is not None:
                if not scholarly.title_matches(previous.title, paper.title) or scholarly._metadata_conflicts(previous, paper):
                    decision['action'] = 'resolved_identity_conflict'
                    outcomes.append(decision)
                    continue
                previous.source_records.extend(paper.source_records)
                if len(paper.abstract or '') > len(previous.abstract or ''):
                    previous.abstract = paper.abstract
            else:
                resolved[key] = paper
            decision['canonical_key'] = key
            for origin in item['origins']:
                group = groups.setdefault(origin['parent_id'], {})
                group[key] = row
        outcomes.append(decision)
    # References resolved via a title and via a DOI can still identify the same
    # work. Merge verified strong aliases before building the semantic inputs.
    temporary = PipelineState(intent=state.intent.model_copy(deep=True))
    canonical = {}
    for key, paper in resolved.items():
        aliased = [temporary.papers[pid] for alias in paper_aliases(paper)
                   for pid in temporary.paper_aliases.get(alias, [])]
        if any(not scholarly.title_matches(paper.title, known.title)
               or scholarly._metadata_conflicts(paper, known) for known in aliased):
            decisions.append({'canonical_key': key, 'action': 'resolved_identity_conflict'})
            continue
        canonical[key], _ = upsert_paper(temporary, paper, source='crossref_reference')
    normalized_groups, normalized_resolved = {}, {}
    for parent_id, group in groups.items():
        normalized_group = {}
        for key in group:
            if key not in canonical:
                continue
            paper = temporary.papers[canonical[key]]
            row = {'paperId': paper.s2_id, 'title': paper.title, 'abstract': paper.abstract,
                   'year': paper.year, 'venue': paper.venue,
                   'externalIds': {'DOI': paper.doi, 'ArXiv': paper.arxiv_id},
                   'authors': [{'name': name} for name in paper.authors],
                   'citationCount': paper.citation_count}
            normalized_key = _key(row)
            normalized_group[normalized_key] = row
            normalized_resolved[normalized_key] = paper
        if normalized_group:
            normalized_groups[parent_id] = normalized_group
    groups, resolved = normalized_groups, normalized_resolved
    audit.update(decisions=decisions + outcomes, resolved_unique_count=len(resolved),
                 parent_group_ids=list(groups), group_count=len(groups),
                 action_counts=dict(Counter(d['action'] for d in decisions + outcomes)),
                 semantic_filter='shared_with_s2_neighbors', reference_budget='no_pre_cut',
                 positive_cap_per_parent=cfg.topk_neighbors_after_lexical,
                 downstream_new_candidate_budget=cfg.citation_new_candidate_limit)
    if run_mgr is not None:
        atomic_write_json(run_mgr.run_dir / 'crossref_reference_expansion.json', audit)
    return [list(group.values()) for group in groups.values()], resolved, audit


def align_crossref_neighbor_groups(s2_groups, reference_groups, resolved):
    """Reconcile strong IDs before scoring; quarantine contradicted identities."""
    aliases = {}
    for key, paper in resolved.items():
        for alias in paper_aliases(paper):
            aliases.setdefault(alias, set()).add(key)
    blocked, conflicts, cleaned = set(), [], []
    for group in s2_groups:
        kept = []
        for neighbor in group or []:
            detail = scholarly.paper_from_s2(neighbor, 'crossref_reference_s2_neighbor')
            # Graph responses are partial; never suppress later complete metadata enrichment.
            detail.source_records[0]['fields'] = 'paperId,title,year,abstract,venue,citationCount,externalIds'
            keys = {key for alias in paper_aliases(detail) for key in aliases.get(alias, ())}
            incompatible = [key for key in keys if not scholarly.title_matches(resolved[key].title, detail.title)
                            or scholarly._metadata_conflicts(resolved[key], detail)]
            if incompatible:
                blocked.update(incompatible)
                conflicts.append({'action': 'cross_source_identity_conflict', 'reference_keys': incompatible,
                                  's2_id': detail.s2_id, 's2_title': detail.title, 's2_doi': detail.doi})
                continue
            for key in keys:
                paper = resolved[key]
                scholarly._apply_shortlist_metadata(paper, detail, scholarly.paper_lookup_id(detail))
                if len(detail.abstract or '') > len(paper.abstract or ''):
                    paper.abstract = detail.abstract
                for alias in paper_aliases(paper):
                    aliases.setdefault(alias, set()).add(key)
            kept.append(neighbor)
        cleaned.append(kept)
    # Remove all occurrences of conflicted IDs, including those fetched earlier.
    conflicted_aliases = {alias for key in blocked for alias in paper_aliases(resolved[key])}
    cleaned = [[row for row in group if not any(alias in conflicted_aliases
                for alias in paper_aliases(scholarly.paper_from_s2(row))) ] for group in cleaned]
    output, rekeyed = [], {}
    for group in reference_groups:
        updated = {}
        for row in group:
            key = _key(row)
            if key in blocked:
                continue
            paper = resolved[key]
            record = {**row, 'paperId': paper.s2_id, 'abstract': paper.abstract,
                      'externalIds': {'DOI': paper.doi, 'ArXiv': paper.arxiv_id}}
            new_key = _key(record)
            updated[new_key] = record
            rekeyed[new_key] = paper
        output.append(list(updated.values()))
    return cleaned, output, rekeyed, conflicts
