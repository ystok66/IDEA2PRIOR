"""Local relevance funnel. No network, model calls, private answers or citation gate."""
from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
import html
import math
import re
import unicodedata

from pipelines.identity import paper_aliases, _conflict, normalize_arxiv


POLICY = 'relevance_protected_v1'
STOP = set('a an the of in on for to and or with by from is are be as at this that we our '
           'paper study approach method using use based can through into both how their'.split())


def tokens(text):
    text = unicodedata.normalize('NFKC', html.unescape(text or ''))
    text = re.sub(r'<[^>]+>', ' ', text).casefold()
    return [w for w in re.findall(r'\w+', text) if len(w) > 1 and w not in STOP]


def retrieval_hits(paper):
    """Only explicit retriever ranks count; LLM memories are not search evidence."""
    for record in paper.source_records:
        if record.get('source') == 'retrieval_hit':
            rank = record.get('rank')
            if type(rank) is int and rank > 0 and record.get('provider'):
                yield record


def local_scores(papers, idea, facets=()):
    """BM25-like local score over complete abstract + boosted title, never citations."""
    query = set(tokens(idea + ' ' + ' '.join(facets)))
    docs = {pid: tokens(p.title) * 3 + tokens(p.abstract) for pid, p in papers.items()}
    df = Counter(w for doc in docs.values() for w in set(doc))
    avg = sum(map(len, docs.values())) / max(1, len(docs)) or 1
    out = {}
    for pid, doc in docs.items():
        counts = Counter(doc)
        score = 0.0
        for word in query & counts.keys():
            tf = counts[word]
            idf = math.log(1 + (len(docs) - df[word] + .5) / (df[word] + .5))
            score += idf * tf * 2.2 / (tf + 1.2 * (.25 + .75 * len(doc) / avg))
        out[pid] = score
    return out


def _entity_groups(papers):
    # Runtime identity registry already keeps conflicting strong IDs apart and
    # binds version records to a shared work. Never fuzzy-merge titles here.
    groups = defaultdict(list)
    for pid, paper in papers.items():
        key = paper.entity_id or pid
        if paper.identity_status == 'conflict':
            key = pid
        groups[key].append(pid)
    return list(groups.values())


def select_candidates(papers, cfg, idea, facets=(), seed_ids=(), limit=None, years=None):
    """Return selected Paper records + a reason for every input, including versions."""
    budget = cfg.max_candidates if limit is None else limit
    budget = max(0, int(budget))
    scores = local_scores(papers, idea, facets)
    reasons = {pid: [] for pid in papers}
    eligible = {pid: p for pid, p in papers.items() if p.identity_status != 'conflict'
                if not (years and p.year is not None and not years[0] <= p.year <= years[1])}
    for pid in papers.keys() - eligible.keys():
        reasons[pid].append('known_identity_conflict' if papers[pid].identity_status == 'conflict'
                            else 'known_year_out_of_scope')
    groups = _entity_groups(eligible)
    reps, members, seed_reps = [], {}, set()
    for group in groups:
        # Choose one evidence-bearing version, retain every version in the audit.
        representative = min(group, key=lambda pid: (
            papers[pid].identity_status == 'conflict', not bool(papers[pid].abstract),
            -scores[pid], -len(papers[pid].abstract or ''), pid))
        reps.append(representative)
        members[representative] = group
        if set(group) & set(seed_ids) and papers[representative].identity_status != 'conflict':
            seed_reps.add(representative)
        for pid in group:
            if pid != representative:
                reasons[pid].append('same_entity_alternate_material:' + representative)
    ranks = {}
    for pid in reps:
        by_provider = {}
        for member in members[pid]:
            for hit in retrieval_hits(papers[member]):
                provider, rank = hit['provider'], hit['rank']
                by_provider[provider] = min(rank, by_provider.get(provider, rank))
        ranks[pid] = by_provider
    rrf = {pid: sum(1 / (60 + rank) for rank in ranks[pid].values()) for pid in reps}
    order = sorted(reps, key=lambda pid: (-scores[pid], -rrf[pid], pid))
    selected, chosen = [], set()

    def take(pid, reason):
        if pid not in chosen and len(selected) < budget:
            selected.append(pid)
            chosen.add(pid)
            reasons[pid].append(reason)

    protected = [pid for pid in order if pid in seed_reps]
    providers = sorted({provider for values in ranks.values() for provider in values})
    # Round-robin across providers so a provider cannot monopolize protections.
    provider_lists = {v: sorted((pid for pid in order if v in ranks[pid]),
                               key=lambda pid: (ranks[pid][v], -scores[pid], pid))[:cfg.retriever_protect_top_k]
                      for v in providers}
    for index in range(cfg.retriever_protect_top_k):
        for provider in providers:
            if index < len(provider_lists[provider]):
                pid = provider_lists[provider][index]
                if papers[pid].identity_status != 'conflict' and pid not in protected:
                    protected.append(pid)
    cap = min(budget, math.ceil(budget * cfg.protected_candidate_ratio)) if budget else 0
    # Allocate a fair share to retrievers even when seeds exceed protection cap.
    seed_budget = min(len(seed_reps), max(1, cap // 2)) if providers else cap
    protected = ([p for p in protected if p in seed_reps][:seed_budget]
                 + [p for p in protected if p not in seed_reps]
                 + [p for p in protected if p in seed_reps][seed_budget:])
    for pid in protected[:cap]:
        take(pid, 'protected_corroborated_seed' if pid in seed_reps else 'protected_retriever_rank')
    for pid in protected[cap:]:
        reasons[pid].append('protection_capacity_exceeded')

    is_recent = lambda pid: bool(papers[pid].year and cfg.recent_year_start <= papers[pid].year <= cfg.recent_year_end)
    recent_min = math.ceil(min(budget, len(reps)) * cfg.recent_min_quota_ratio)
    recent_max = math.floor(min(budget, len(reps)) * cfg.recent_max_quota_ratio)
    # A minimum, not the old eager fill-to-maximum behavior.
    for pid in order:
        if sum(is_recent(p) for p in chosen) >= recent_min:
            break
        if is_recent(pid):
            take(pid, 'recent_minimum_by_relevance')
    missing_kept = sum(not papers[p].abstract for p in chosen)
    for pid in sorted(order, key=lambda p: (-rrf[p], -scores[p], p)):
        if missing_kept >= cfg.missing_abstract_reserve:
            break
        if not papers[pid].abstract and pid not in chosen and papers[pid].identity_status != 'conflict':
            take(pid, 'missing_abstract_uncertainty_reserve')
            missing_kept = sum(not papers[p].abstract for p in chosen)
    for pid in order:
        if is_recent(pid) and sum(is_recent(p) for p in chosen) >= recent_max:
            continue
        take(pid, 'local_relevance')
    # If there is insufficient non-recent material, keep budget coverage visible
    # instead of silently returning a needlessly tiny pool.
    for pid in order:
        take(pid, 'recent_cap_relaxed_to_fill_budget')
    selected.sort(key=lambda pid: (-scores[pid], -rrf[pid], pid))
    for pid in reps:
        if pid not in chosen:
            reasons[pid].append('below_candidate_budget')
    audit = {'policy_version': POLICY, 'before_ids': list(papers), 'selected_ids': selected,
             'dropped_ids': [pid for pid in papers if pid not in chosen],
             'budget': budget, 'year_scope': years, 'representative_members': members,
             'recent_selected': sum(is_recent(pid) for pid in selected),
             'recent_min_target': recent_min, 'recent_max_target': recent_max,
             'decisions': {pid: {'selected': pid in chosen, 'reasons': reasons[pid],
                                'local_relevance': scores[pid], 'citation_used': False,
                                'retriever_ranks': ranks.get(pid, {})} for pid in papers}}
    return [papers[pid] for pid in selected], audit


def match_order(match):
    return (-(match.coverage_level if match.coverage_level is not None else -1),
            -match.task_similarity, -match.method_similarity,
            -len(match.covered_facet_labels), -match.high_level_facet_score,
            -match.structural_score, match.paper_id)


def select_shortlist(papers, matches, cfg, idea, facets=(), years=None):
    """50 quality candidates + bounded uncertain/boundary reserve; no gold tie break."""
    eligible = {pid: p for pid, p in papers.items() if p.identity_status != 'conflict'
                and not (years and p.year is not None and not years[0] <= p.year <= years[1])}
    scored = sorted((m for m in matches if m.paper_id in eligible and
                     m.scoring_status == 'succeeded' and m.coverage_level is not None), key=match_order)
    selected, seen = [], set()
    def take(pid):
        entity = papers[pid].entity_id or pid
        if entity not in seen:
            seen.add(entity)
            selected.append(pid)
    for match in scored:
        if len(selected) >= cfg.metadata_shortlist_size:
            break
        take(match.paper_id)
    primary = list(selected)
    scores = local_scores(eligible, idea, facets)
    assessed = {m.paper_id for m in scored}
    # Use half the buffer for unassessed/abstract-missing evidence, then score-boundary.
    uncertain = sorted((pid for pid in eligible if pid not in assessed or not papers[pid].abstract),
                       key=lambda pid: (-scores[pid], pid))
    budget = cfg.metadata_shortlist_size + cfg.metadata_boundary_buffer
    uncertainty_budget = min(budget, len(selected) + math.ceil(cfg.metadata_boundary_buffer / 2))
    for pid in uncertain:
        if len(selected) >= uncertainty_budget:
            break
        take(pid)
    for pid in [m.paper_id for m in scored] + uncertain:
        if len(selected) >= budget:
            break
        take(pid)
    return selected, {'policy_version': POLICY, 'primary_ids': primary,
                      'buffer_ids': [p for p in selected if p not in primary],
                      'selected_ids': selected, 'enrichment_budget': budget,
                      'not_selected_ids': [p for p in papers if p not in selected]}


def refresh_aliases(state):
    """Reconcile work identity after enrichment without merging material or scores.

    Distinct S2-only/arXiv-only records can become linked by newly obtained strong
    IDs. They should share a work slot, but retain separate PDFs, text and record
    IDs. Require pairwise compatibility across the whole component: a sparse-ID
    intermediary cannot bridge two mutually conflicting strong identities.
    """
    for pid, paper in state.papers.items():
        for alias in paper_aliases(paper):
            if alias not in paper.aliases:
                paper.aliases.append(alias)
            ids = state.paper_aliases.setdefault(alias, [])
            if pid not in ids:
                ids.append(pid)
    active = {pid: p for pid, p in state.papers.items() if p.identity_status != 'conflict'}
    parent = {pid: pid for pid in active}
    members = {pid: {pid} for pid in active}
    buckets = defaultdict(list)
    for pid, paper in active.items():
        for alias in paper_aliases(paper):
            buckets[alias].append(pid)
        if paper.entity_id:
            buckets['entity:' + paper.entity_id].append(pid)

    def find(pid):
        while parent[pid] != pid:
            parent[pid] = parent[parent[pid]]
            pid = parent[pid]
        return pid

    def compatible(left, right):
        a, b = active[left], active[right]
        wa = re.sub(r'v\d+$', '', normalize_arxiv(a.arxiv_id) or '')
        wb = re.sub(r'v\d+$', '', normalize_arxiv(b.arxiv_id) or '')
        return not _conflict(a, b) and not (wa and wb and wa != wb)

    for alias, ids in sorted(buckets.items()):
        for left, right in combinations(ids, 2):
            a, b = find(left), find(right)
            if a == b:
                continue
            if not all(compatible(x, y) for x in members[a] for y in members[b]):
                issue = {'reason': 'post_enrichment_alias_conflict', 'alias': alias,
                         'paper_ids': sorted(members[a] | members[b])}
                if issue not in state.identity_conflicts:
                    state.identity_conflicts.append(issue)
                continue
            parent[b] = a
            members[a].update(members.pop(b))
    for group in members.values():
        if len(group) < 2:
            continue
        entity = min(active[pid].entity_id or pid for pid in group)
        for pid in group:
            paper = active[pid]
            if paper.entity_id != entity:
                paper.source_records.append({'source': 'entity_reconciliation',
                                             'previous_entity_id': paper.entity_id,
                                             'entity_id': entity, 'paper_ids': sorted(group)})
                paper.entity_id = entity
