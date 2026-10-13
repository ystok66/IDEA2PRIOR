"""Validated final decisions; material availability never stands in for relevance."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, is_dataclass
import hashlib
import json
import math
from typing import Any


def valid_level(value: Any) -> bool:
    return type(value) is int and 0 <= value <= 3


def valid_similarity(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def bucket_for(level: int | None) -> str:
    return {3: 'high_overlap', 2: 'partial_overlap', 1: 'adjacent', 0: 'ignored'}.get(level, 'unassessed') if valid_level(level) else 'unassessed'


def build_assessment_snapshot(state, matches, facets, *, evidence=None) -> dict[str, Any]:
    valid_facets = {f'F{i+1}' for i in range(len(facets))}
    by_id = {m.paper_id: m for m in matches or []}
    entries = {}
    buckets = {name: [] for name in ['high_overlap', 'partial_overlap', 'adjacent', 'ignored', 'unassessed']}
    for pid, paper in state.papers.items():
        match = by_id.get(pid)
        status, error, level = 'not_scored', None, None
        if match:
            status = getattr(match, 'scoring_status', 'succeeded')
            error = getattr(match, 'scoring_error', None)
            if status == 'succeeded':
                labels = getattr(match, 'covered_facet_labels', []) or []
                if (not valid_level(match.coverage_level) or not set(labels).issubset(valid_facets)
                        or not valid_similarity(match.task_similarity) or not valid_similarity(match.method_similarity)):
                    status, error = 'invalid_output', 'Invalid coverage, similarity, or facet reference'
                else:
                    level = match.coverage_level
        refinement = getattr(match, 'refinement_status', 'not_run') if match else 'not_run'
        source = ('fulltext_snippets' if refinement == 'succeeded' else 'abstract') if level is not None else None
        abstract_level = getattr(match, 'abstract_coverage_level', None) if match else None
        if abstract_level is None and source == 'abstract':
            abstract_level = level
        bucket = bucket_for(level)
        buckets[bucket].append(pid)
        entries[pid] = {
            'paper_id': pid, 'entity_id': getattr(paper, 'entity_id', None),
            'document_version': getattr(paper, 'document_version', None),
            'content_hash': getattr(paper, 'content_hash', None),
            'parser_version': getattr(paper, 'parser_version', None),
            'status': status, 'error': error,
            'abstract_assessment': {'coverage_level': abstract_level, 'facet_labels': list(getattr(match, 'covered_facet_labels', []) or []), 'notes': getattr(match, 'abstract_notes', '') or (getattr(match, 'notes', '') if source == 'abstract' else '')},
            'fulltext_assessment': {'status': refinement, 'coverage_level': getattr(match, 'refined_coverage_level', None), 'error': getattr(match, 'refinement_error', None), 'snippets': list(getattr(match, 'refinement_snippets', []) or [])},
            'final': {'coverage_level': level, 'bucket': bucket, 'source': source, 'notes': getattr(match, 'notes', '') if match else '', 'role': getattr(match, 'role', 'unknown') if match else 'unknown'},
            'material': {'pdf_status': paper.pdf_status, 'section_status': paper.section_status, 'has_chunks': bool(paper.chunks), 'has_sections': bool(getattr(paper, 'parsed_sections', []))},
        }
    counts = Counter(entry['status'] for entry in entries.values())
    snapshot = {'schema_version': 'assessment-v1', 'idea_spec': state.idea_spec,
                'candidate_ids': list(state.papers), 'facets': [{'id': f'F{i+1}', 'definition': f} for i, f in enumerate(facets)],
                'entries': entries, 'buckets': buckets, 'status_counts': dict(counts),
                'rule_evidence': [asdict(e) if is_dataclass(e) else e for e in evidence or []]}
    assert sum(len(v) for v in buckets.values()) == len(entries)
    snapshot['snapshot_id'] = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    return snapshot


def effective_levels(state, scored_matches=None, coverage_levels=None, snapshot=None):
    if snapshot is not None:
        return {pid: snapshot['entries'][pid]['final']['coverage_level'] for pid in state.papers}
    if scored_matches is not None:
        by_id = {m.paper_id: m for m in scored_matches}
        return {pid: (by_id[pid].coverage_level if pid in by_id and getattr(by_id[pid], 'scoring_status', 'succeeded') == 'succeeded' and valid_level(by_id[pid].coverage_level) else None) for pid in state.papers}
    return {pid: ((coverage_levels or {}).get(pid) if valid_level((coverage_levels or {}).get(pid)) else None) for pid in state.papers}
