"""Export either deterministic baseline or validated Expert order, without calls."""
import copy
from pathlib import Path


def rank_candidates(snapshot, registry, years=None):
    # Preserve the predeclared pilot rule, including stable ties and unknowns.
    ids = snapshot["candidate_ids"]

    def level(pid):
        n = snapshot["entries"][pid]["final"]["coverage_level"]
        return n if type(n) is int and 0 <= n <= 3 else -1

    ranked = sorted(ids, key=lambda pid: -level(pid))
    result, seen = [], set()
    for pid in ranked:
        paper = registry["papers"].get(pid, {})
        if years and type(paper.get('year')) is int and not years[0] <= paper['year'] <= years[1]:
            continue
        identity = paper.get("entity_id") or pid
        if identity in seen:
            continue
        seen.add(identity)
        final = snapshot["entries"][pid]["final"]
        doi, arxiv = paper.get("doi"), paper.get("arxiv_id")
        url = ("https://doi.org/" + doi.removeprefix("https://doi.org/") if doi else "https://arxiv.org/abs/" + arxiv if arxiv else 'https://www.semanticscholar.org/paper/' + paper['s2_id'] if paper.get('s2_id') else 'https://openalex.org/' + paper['openalex_id'].rsplit('/', 1)[-1] if paper.get('openalex_id') else None)
        result.append({"rank": len(result) + 1, "paper_id": pid, "title": paper.get("title"), "authors": paper.get("authors", []), "year": paper.get("year"), "venue": paper.get("venue"), "url": url, "coverage_level": final["coverage_level"], "match_explanation": final["notes"], "evidence_source": final["source"], "identity_adjudication": "PENDING", "field_conflicts": paper.get("field_conflicts", {})})
        if len(result) == 5:
            break
    return result


def export_ranked_candidates(snapshot, registry, output_dir, years=None, expert_ranking=None):
    """Save evaluation results without generating a research report."""
    from pipelines.runtime import atomic_write_json
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if expert_ranking is None:
        candidates = rank_candidates(snapshot, registry, years=years)
        ranking_rule = 'coverage_desc_then_final_candidate_order_scoped_v2' if years else 'coverage_desc_then_final_candidate_order_v1'
        ranking_metadata = {}
    else:
        status = expert_ranking['status']
        if status not in ('SUCCEEDED', 'FAILED', 'NOT_RUN_EMPTY_POOL'):
            raise ValueError('Unrecognized Expert ranking status')
        candidates = copy.deepcopy(expert_ranking.get('candidates', [])) if status == 'SUCCEEDED' else []
        ranking_rule = 'expert_shortlist_fulltext5_v1'
        ranking_metadata = {'status': status,
            'ranking_candidate_ids': expert_ranking.get('candidate_ids', expert_ranking.get('candidate_pool_ids', [])),
            'attempted_fulltext_ids': expert_ranking.get('attempted_fulltext_ids', []),
            'pdf_text_policy': copy.deepcopy(expert_ranking.get('pdf_text_policy')),
            'error_type': expert_ranking.get('error_type')}
    atomic_write_json(output_dir / 'ranking_scope.json', {
        'years': years,
        'excluded_out_of_scope': [pid for pid, p in registry['papers'].items() if years and type(p.get('year')) is int and not years[0] <= p['year'] <= years[1]],
        'unknown_year_ids': [pid for pid, p in registry['papers'].items() if p.get('year') is None],
        **({'ranking_candidate_ids': ranking_metadata['ranking_candidate_ids'],
            'pdf_text_policy': ranking_metadata['pdf_text_policy']} if expert_ranking is not None else {}),
    })
    atomic_write_json(output_dir / 'ranked_candidates.json', {
        'ranking_rule': ranking_rule,
        'snapshot_id': snapshot['snapshot_id'], 'candidates': candidates,
        **ranking_metadata,
    })
    rule_description = ('Expert comparison of the frozen shortlist, including available PDF evidence from five fixed attempts. Coverage grades remain the earlier assessments. Identity adjudication pending.'
        if expert_ranking is not None else 'Fixed ordering: overall overlap grade descending, then final candidate order. Identity adjudication pending.')
    lines = ['# Ranked candidates', '', rule_description, '']
    if expert_ranking is not None:
        lines.extend([f"Expert ranking status: {ranking_metadata['status']}", ''])
        text_policy = ranking_metadata.get('pdf_text_policy') or {}
        if text_policy.get('mode') == 'snippets':
            lines.extend(['PDF evidence consists of selected excerpts; unselected content has not been reviewed by Expert.', ''])
    for c in candidates:
        lines.extend([f"## {c['rank']}. {c['title'] or '[missing title]'}", '', f"Authors: {', '.join(c['authors']) or '[unknown]'}", f"Year: {c['year']}; venue: {c['venue'] or '[unknown]'}", f"URL: {c['url'] or '[unverified / unavailable]'}", f"Match: {c['match_explanation'] or '[no valid assessment]'}", f"Evidence source: {c['evidence_source']}; identity adjudication: PENDING", ''])
        if c.get('expert_reason') and c['expert_reason'] != c['match_explanation']:
            lines.extend([f"Expert ranking reason: {c['expert_reason']}", ''])
        if c.get('abstract_match_explanation'):
            lines.extend([f"Earlier abstract assessment: {c['abstract_match_explanation']}", ''])
        if c.get('evidence_scope'):
            lines.extend([f"Evidence scope: {c['evidence_scope']}", ''])
    if not candidates:
        lines.append('Expert ranking failed; no fallback ranking was substituted. See expert_ranking.json.'
            if expert_ranking is not None and ranking_metadata['status'] == 'FAILED'
            else 'No candidates were available. This is not an adjudicated target miss.')
    (output_dir / 'ranked_candidates.md').write_text('\n'.join(lines), encoding='utf-8')
    return len(candidates)
