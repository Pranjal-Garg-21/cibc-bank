"""Acceptance boundaries for local matching and generated refusal/join hints.

These synthetic tests do not modify or supply examples to eval/router_cases.csv.
"""
from datetime import date
from types import SimpleNamespace

import duckdb
import pytest
import yaml

from trustissues.ask import metric_matcher as M, refusal, router, service
from trustissues.ask.access import query_policy
from trustissues.ask.data.schema_graph import SEMANTIC_JOIN_KEYS
from trustissues.ask.data.semantic import create_semantic_fixtures
from trustissues.ask.join_context import prompt_join_context
from trustissues.ask.phrase_index import PhraseIndex
from trustissues.ask.registry_phrases import field_phrases, generate
from trustissues.ask.sql_fallback import run_fallback
from trustissues.ask.types import UserContext


def candidates(first, second):
    return (M.Candidate('average_dpd','Average days past due',first),
            M.Candidate('delinquency_rate','Delinquency rate',second))


@pytest.mark.parametrize('first,second,chosen',[(0.90,0.70,'average_dpd'),(0.50,0.30,None),(0.90,0.89,None)])
def test_confidence_and_margin_must_both_pass(first,second,chosen):
    result=M.decide(candidates(first,second))
    assert result.metric_id == chosen
    if chosen is None:
        assert 'Average days past due' in result.clarification and 'Delinquency rate' in result.clarification


def test_retrieval_cannot_add_an_unapproved_metric():
    with pytest.raises(ValueError,match='outside the catalogue'):
        M.decide((M.Candidate('invented_rate','Made up',1.0),*candidates(.9,.7)))


def test_clarification_card_contains_both_choices_and_does_not_execute(monkeypatch):
    monkeypatch.setattr(router,'match_metric',lambda q:M.decide(candidates(.9,.89)))
    result=service.ask('Compare account movement in September 2026',UserContext('lead','team_lead'),
                       as_of_date=date(2026,9,28))
    assert result.path=='cannot_answer' and not result.refused and result.sql is None
    assert result.answer == 'Which metric do you mean: Average days past due or Delinquency rate?'


def test_semantic_match_still_uses_normal_access_checks(monkeypatch):
    monkeypatch.setattr(router,'match_metric',lambda q:M.decide(candidates(.9,.7)))
    result=service.ask('Compare account movement',UserContext('agent','agent'),as_of_date=date(2026,9,28))
    assert result.refused and result.refusal_reason=='access' and result.sql is None


def test_known_policy_and_entity_counts_do_not_reach_semantic_matching(monkeypatch):
    def forbidden(_):
        raise AssertionError('This intent should not enter metric retrieval')
    monkeypatch.setattr(router,'match_metric',forbidden)
    assert router.route('How many open collections cases by queue?').path=='sql'
    assert router.route('What must an agent do about a payment plan?').path=='rag_corpus'


def test_model_loader_is_cpu_only_local_and_profiles_cover_catalogue(monkeypatch):
    fastembed = pytest.importorskip('fastembed')
    import numpy as np
    from trustissues.ask.data.metric_templates import METRICS
    calls=[]
    class Fake:
        def __init__(self,**kwargs): calls.append(kwargs)
        def passage_embed(self,texts): return [np.array([1.0,0.0]) for _ in texts]
    M._index.cache_clear()
    monkeypatch.setattr(fastembed,'TextEmbedding',Fake)
    try:
        _,owners,_=M._index()
        assert {r['metric_id'] for r in owners} == METRICS
        assert calls[0]['local_files_only'] is True
        assert calls[0]['providers']==['CPUExecutionProvider']
    finally:
        M._index.cache_clear()


def test_missing_optional_model_preserves_rule_router(monkeypatch):
    def missing():raise ImportError('optional model not installed')
    M.match.cache_clear()
    monkeypatch.setattr(M,'_index',missing)
    try:
        assert M.match('an unfamiliar analytic request') is None
    finally:
        M.match.cache_clear()


def test_registry_new_fields_are_generated_without_broad_component_bans(tmp_path):
    path=tmp_path/'sensitivity.yaml'
    path.write_text(yaml.safe_dump({'tables':{'customers':{'special_protected_trait':'protected',
                       'agent_display_name':'direct_pii','account_age_days':'none'}}}))
    phrases=generate(path,{'protected'})
    assert 'special protected trait' in phrases
    assert 'agent display name' not in phrases and 'account age days' not in phrases
    assert 'agent' not in field_phrases('agent_display_name')


def test_generated_pii_requires_disclosure_intent():
    assert refusal.check('Export cardholder_name_raw') is not None
    assert refusal.check('How should an agent verify cardholder_name_raw?') is None


@pytest.mark.parametrize('q,expected',[
    ('gender',True),('gendered wording',False),('the average',False),
    ('AGE',True),('ageing buckets',False),('unknown_gender',False),
    ('(gender)',True),('age of account',True),
])
def test_automaton_preserves_word_boundaries(q,expected):
    index=PhraseIndex([('protected','gender'),('protected','age')])
    assert bool(index.hits(q)) is expected


def test_empty_automaton_and_shared_phrases():
    assert PhraseIndex([]).hits('anything')==()
    hits=PhraseIndex([('first','trait'),('second','trait')]).hits('trait')
    assert {h.category for h in hits}=={'first','second'}


def test_missing_optional_automaton_does_not_disable_registry_refusals(monkeypatch):
    import builtins
    original=builtins.__import__
    def without_automaton(name,*args,**kwargs):
        if name=='ahocorasick':raise ImportError('optional accelerator not installed')
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',without_automaton)
    index=PhraseIndex([('protected','gender')])
    assert index.automaton is None
    assert {hit.category for hit in index.hits('GENDER')}=={'protected'}
    assert index.hits('gendered')==()


def test_join_context_uses_existing_approved_edges_and_steiner_path():
    hint=prompt_join_context('Compare contacts and promises for cases')
    assert 'Approximate Steiner path' in hint
    assert 'multiply rows' in hint and 'No customer-level joins' in hint
    for left,lc,right,rc in SEMANTIC_JOIN_KEYS:
        assert f'{left}.{lc} = {right}.{rc}' in hint
    assert 'No approved connected path' in prompt_join_context('Compare accounts and contacts')


def test_fallback_prompt_contains_graph_hints_without_relaxing_policy():
    calls=[]
    class Scripted:
        def complete(self,**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(data={'sql':'SELECT COUNT(*) AS n FROM gold.semantic_cases','reason':''})
    user=UserContext('lead','team_lead')
    policy=query_policy(user)
    with duckdb.connect(':memory:') as con:
        create_semantic_fixtures(con)
        result=run_fallback('Compare contacts and promises for cases',llm=Scripted(),policy=policy,
                            as_of=date(2026,9,28),con=con)
    assert result.result.rows==((0,),)
    assert 'Approximate Steiner path' in calls[0]['system']
    assert policy.join_keys==SEMANTIC_JOIN_KEYS
